#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股每日短线复盘：抓取公开数据并生成 HTML 报告。

用法:
    python3 daily_review.py                          # 复盘今天（需收盘后运行）
    python3 daily_review.py --date 20260806          # 指定交易日
    python3 daily_review.py --date 20260806 --out /path/to/dir

数据源:
    东财 push2ex 涨停/炸板/跌停/昨涨停池、东财数据中心龙虎榜与席位、
    腾讯财经指数、新浪行情成交额/跌幅/涨跌家数、同花顺热点题材(可选)。
"""

import argparse
import json
import math
import random
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# 东财统一会话：绕过系统代理直连，串行限流防封
EM_SESSION = requests.Session()
EM_SESSION.trust_env = False
EM_SESSION.headers.update({"User-Agent": UA})
EM_MIN_INTERVAL = 1.0
_em_last_call = [0.0]


def em_get(url, params=None, headers=None, timeout=15, **kwargs):
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.4))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"
SINA_BASE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center."


def eastmoney_datacenter(report_name, filter_str="", page_size=500,
                         sort_columns="", sort_types="-1", max_pages=10):
    rows = []
    for page in range(1, max_pages + 1):
        params = {
            "reportName": report_name, "columns": "ALL", "filter": filter_str,
            "pageNumber": str(page), "pageSize": str(page_size),
            "sortColumns": sort_columns, "sortTypes": sort_types,
            "source": "WEB", "client": "WEB",
        }
        r = em_get(DATACENTER_URL, params=params, timeout=15)
        batch = (r.json().get("result") or {}).get("data") or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
    return rows


def _fmt_zt_time(t):
    s = str(t).zfill(6)
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"


def _em_zt_api(endpoint, sort, date_str):
    url = f"https://push2ex.eastmoney.com/{endpoint}"
    params = {"ut": ZTB_UT, "dpt": "wz.ztzt", "Pageindex": 0,
              "pagesize": 10000, "sort": sort, "date": date_str}
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        return (r.json().get("data") or {}).get("pool") or []
    except Exception as e:
        print(f"[WARN] 涨停板池 {endpoint} 请求失败: {e}")
        return []


def fetch_zt_pools(date_str):
    zt = []
    for p in _em_zt_api("getTopicZTPool", "fbt:asc", date_str):
        zt.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                   "pct": round(p["zdp"], 2), "amount": p["amount"], "float_cap": p["ltsz"],
                   "turnover": round(p["hs"], 2), "limit_days": p["lbc"],
                   "first_seal": _fmt_zt_time(p["fbt"]), "last_seal": _fmt_zt_time(p["lbt"]),
                   "seal_fund": p["fund"], "break_times": p["zbc"], "industry": p.get("hybk", ""),
                   "zt_stat": f'{(p.get("zttj") or {}).get("days", "?")}天{(p.get("zttj") or {}).get("ct", "?")}板'})

    zb = []
    for p in _em_zt_api("getTopicZBPool", "fbt:asc", date_str):
        zb.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                   "limit_price": p["ztp"] / 1000, "pct": round(p["zdp"], 2),
                   "turnover": round(p["hs"], 2), "first_seal": _fmt_zt_time(p["fbt"]),
                   "break_times": p["zbc"], "amplitude": round(p["zf"], 2),
                   "speed": round(p["zs"], 2), "industry": p.get("hybk", ""),
                   "zt_stat": f'{(p.get("zttj") or {}).get("days", "?")}天{(p.get("zttj") or {}).get("ct", "?")}板'})

    dt = []
    for p in _em_zt_api("getTopicDTPool", "fund:asc", date_str):
        dt.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                   "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2), "pe": p.get("pe"),
                   "seal_fund": p["fund"], "last_seal": _fmt_zt_time(p["lbt"]),
                   "board_amount": p.get("fba"), "dt_days": p.get("days"),
                   "open_times": p.get("oc"), "industry": p.get("hybk", "")})

    yzt = []
    for p in _em_zt_api("getYesterdayZTPool", "zs:desc", date_str):
        yzt.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2),
                    "amplitude": round(p["zf"], 2), "speed": round(p["zs"], 2),
                    "y_first_seal": _fmt_zt_time(p["yfbt"]), "y_limit_days": p["ylbc"],
                    "industry": p.get("hybk", ""),
                    "zt_stat": f'{(p.get("zttj") or {}).get("days", "?")}天{(p.get("zttj") or {}).get("ct", "?")}板'})

    return zt, zb, dt, yzt


def industry_comparison(top_n=10):
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {"pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
              "fs": "m:90+t:2", "fields": "f2,f3,f4,f12,f14,f104,f105,f128,f136,f140"}
    items = []
    for attempt in range(2):
        try:
            r = em_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
            items = (r.json().get("data") or {}).get("diff") or []
            if items:
                break
        except Exception as e:
            if attempt == 0:
                time.sleep(2.0)
            else:
                print(f"[WARN] 行业板块请求失败: {e}")
    rows = []
    for i, it in enumerate(items):
        rows.append({"rank": i + 1, "name": it.get("f14"), "change_pct": it.get("f3"),
                     "up": it.get("f104"), "down": it.get("f105"), "leader": it.get("f140"),
                     "leader_change": it.get("f136")})
    return {"top": rows[:top_n], "bottom": rows[-top_n:], "total": len(rows)}


def tencent_quotes_prefixed(entries):
    url = "https://qt.gtimg.cn/q=" + ",".join(entries)
    r = EM_SESSION.get(url, timeout=10)
    data = r.content.decode("gbk")
    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        result[vals[1]] = {
            "code": key[2:],
            "price": float(vals[3]) if vals[3] else 0,
            "change_pct": float(vals[32]) if vals[32] else 0,
            "amount_wan": float(vals[37]) if vals[37] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "high": float(vals[33]) if vals[33] else 0,
            "low": float(vals[34]) if vals[34] else 0,
        }
    return result


_snapshot_date_cache = None


def current_session_date():
    """腾讯当前快照所属交易日（YYYYMMDD），用于判断实时快照与目标日期是否一致。"""
    global _snapshot_date_cache
    if _snapshot_date_cache is not None:
        return _snapshot_date_cache
    try:
        r = EM_SESSION.get("https://qt.gtimg.cn/q=sh000001", timeout=10)
        data = r.content.decode("gbk")
        line = data.strip().split(";")[0]
        if '"' in line:
            vals = line.split('"')[1].split("~")
            if len(vals) > 30 and len(vals[30]) >= 8:
                _snapshot_date_cache = vals[30][:8]
                return _snapshot_date_cache
    except Exception as e:
        print(f"[WARN] 获取当前交易日失败: {e}")
    return None


def snapshot_is_target(date_str):
    return date_str == current_session_date()


def tencent_quotes_for_date(entries, date_str):
    """按目标交易日取指数收盘。目标日=当前快照日时用实时快照，否则用腾讯日K历史收盘。"""
    if snapshot_is_target(date_str):
        return tencent_quotes_prefixed(entries)
    date_dash = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    names = {}
    try:
        r = EM_SESSION.get("https://qt.gtimg.cn/q=" + ",".join(entries), timeout=10)
        for line in r.content.decode("gbk").strip().split(";"):
            if '"' not in line:
                continue
            key = line.split("=")[0].split("_")[-1]
            vals = line.split('"')[1].split("~")
            if len(vals) >= 3:
                names[key] = vals[1]
    except Exception as e:
        print(f"[WARN] 指数名称获取失败: {e}")
    result = {}
    for secid in entries:
        try:
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={secid},day,,,60,qfq"
            r = EM_SESSION.get(url, timeout=10)
            node = (r.json().get("data") or {}).get(secid) or {}
            rows = node.get("day") or node.get("qfqday") or []
            for i, row in enumerate(rows):
                if len(row) < 5 or row[0] != date_dash:
                    continue
                prev_close = float(rows[i - 1][2]) if i > 0 and len(rows[i - 1]) >= 3 else 0.0
                price = float(row[2])
                pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0
                result[names.get(secid, secid)] = {
                    "price": price,
                    "change_pct": round(pct, 2),
                    "amount_wan": float(row[6]) / 1e4 if len(row) > 6 and row[6] else 0,
                    "turnover_pct": 0.0,
                    "high": float(row[3]) if row[3] else price,
                    "low": float(row[4]) if row[4] else price,
                }
                break
        except Exception as e:
            print(f"[WARN] 指数 {secid} 历史K线失败: {e}")
    if not result:
        print("[WARN] 指数历史数据缺失，回退当前实时快照")
        return tencent_quotes_prefixed(entries)
    return result


def sina_get(url, params=None, retries=3):
    last = None
    for i in range(retries):
        try:
            r = EM_SESSION.get(url, params=params, headers={"User-Agent": UA}, timeout=12)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:
            last = e
        time.sleep(1.0 * (i + 1))
    raise last


def sina_page(sort, asc, page, num=100):
    return sina_get(SINA_BASE + "getHQNodeData",
                    {"page": page, "num": num, "sort": sort, "asc": asc,
                     "node": "hs_a", "symbol": "", "_s_r_a": "page"}) or []


def fetch_sorted(sort, asc, n):
    rows = []
    page = 1
    while len(rows) < n:
        diff = sina_page(sort, asc, page)
        rows.extend(diff)
        if not diff or len(rows) >= n:
            break
        page += 1
    return rows[:n]


def is_bond_code(code):
    """可转债等债券代码，避免混入股票龙虎榜/席位统计。"""
    return str(code).startswith(("11", "12", "13"))


def short_seat_name(name):
    n = str(name)
    for frag in ("证券有限责任公司", "证券股份有限公司", "股份有限公司", "有限责任公司",
                 "证券营业部", "证券分公司", "(中国)", "（中国）", "上海浦东新区"):
        n = n.replace(frag, "")
    if "上海花园石桥路" in n:
        n = n.replace("上海花园石桥路", "花园石桥")
    return n


def snapshot_cache_path(out_dir, date_str):
    return Path(out_dir) / f"review_snapshot_{date_str}.json"


def load_review_snapshot(out_dir, date_str):
    p = snapshot_cache_path(out_dir, date_str)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if data.get("date") == date_str else None


def save_review_snapshot(out_dir, date_str, m):
    payload = {"date": date_str, "amount_top": m["amount_top"],
               "breadth": m["breadth"], "drops": m["drops"]}
    p = snapshot_cache_path(out_dir, date_str)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def market_breadth():
    raw = sina_get(SINA_BASE + "getHQNodeStockCount", {"node": "hs_a"})
    if isinstance(raw, str):
        try:
            total = int(raw.strip('"'))
        except (TypeError, ValueError):
            return None
    else:
        total = (raw or {}).get("count") or 0
    if not total:
        return None
    pages = math.ceil(total / 100)
    lo, hi = 1, pages
    while lo < hi:
        mid = (lo + hi) // 2
        diff = sina_page("changepercent", 0, mid)
        vals = []
        for it in diff:
            try:
                vals.append(float(it.get("changepercent")))
            except (TypeError, ValueError):
                pass
        if vals and min(vals) >= 0:
            lo = mid + 1
        else:
            hi = mid
    up = flat = down = 0
    if lo <= pages:
        diff = sina_page("changepercent", 0, lo)
        for it in diff:
            try:
                v = float(it.get("changepercent"))
            except (TypeError, ValueError):
                continue
            if v > 0:
                up += 1
            elif v == 0:
                flat += 1
            else:
                down += 1
        up += (lo - 1) * 100
    else:
        up = total
    return {"total": total, "up": up, "flat": flat, "down": total - up - flat}


def daily_dragon_tiger(trade_date):
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
        page_size=500, sort_columns="BILLBOARD_NET_AMT", sort_types="-1")
    stocks = []
    for row in data:
        net = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
        stocks.append({
            "code": row.get("SECURITY_CODE", ""), "name": row.get("SECURITY_NAME_ABBR", ""),
            "reason": row.get("EXPLANATION", ""), "close": row.get("CLOSE_PRICE") or 0,
            "change_pct": round(float(row.get("CHANGE_RATE") or 0), 2),
            "net_buy_wan": round(net, 1),
            "buy_wan": round((row.get("BILLBOARD_BUY_AMT") or 0) / 10000, 1),
            "sell_wan": round((row.get("BILLBOARD_SELL_AMT") or 0) / 10000, 1),
            "turnover_pct": round(float(row.get("TURNOVERRATE") or 0), 2),
        })
    return stocks


def daily_seats(trade_date):
    buy_rows = eastmoney_datacenter("RPT_BILLBOARD_DAILYDETAILSBUY",
                                    filter_str=f"(TRADE_DATE='{trade_date}')",
                                    page_size=500, sort_columns="BUY", sort_types="-1")
    sell_rows = eastmoney_datacenter("RPT_BILLBOARD_DAILYDETAILSSELL",
                                     filter_str=f"(TRADE_DATE='{trade_date}')",
                                     page_size=500, sort_columns="SELL", sort_types="-1")
    seat_map = defaultdict(lambda: {"name": "", "inst_buy": 0, "inst_sell": 0,
                                    "buy_seats": [], "sell_seats": []})
    for row in buy_rows:
        code = row.get("SECURITY_CODE", "")
        if is_bond_code(code):
            continue
        m = seat_map[code]
        m["name"] = row.get("SECURITY_NAME_ABBR", "")
        if str(row.get("OPERATEDEPT_CODE", "")) == "0":
            m["inst_buy"] += row.get("BUY") or 0
        else:
            m["buy_seats"].append({"seat": row.get("OPERATEDEPT_NAME", ""), "amt": row.get("BUY") or 0})
    for row in sell_rows:
        code = row.get("SECURITY_CODE", "")
        if is_bond_code(code):
            continue
        m = seat_map[code]
        m["name"] = row.get("SECURITY_NAME_ABBR", "")
        if str(row.get("OPERATEDEPT_CODE", "")) == "0":
            m["inst_sell"] += row.get("SELL") or 0
        else:
            m["sell_seats"].append({"seat": row.get("OPERATEDEPT_NAME", ""), "amt": row.get("SELL") or 0})
    inst_stocks = []
    for code, m in seat_map.items():
        if is_bond_code(code) or "转债" in m["name"]:
            continue
        ib, ise = m["inst_buy"] / 1e4, m["inst_sell"] / 1e4
        if ib or ise:
            inst_stocks.append({"code": code, "name": m["name"],
                                "inst_buy_wan": round(ib, 0), "inst_sell_wan": round(ise, 0),
                                "inst_net_wan": round(ib - ise, 0)})
    inst_stocks.sort(key=lambda x: x["inst_net_wan"], reverse=True)
    seat_counter = Counter()
    for code, m in seat_map.items():
        for b in m["buy_seats"]:
            if b["seat"] in ("机构投资者", "自然人", "其他自然人"):
                continue
            seat_counter[short_seat_name(b["seat"])] += b["amt"]
    return {
        "inst_stocks": inst_stocks,
        "hot_seats": dict(seat_counter.most_common(8)),
    }


def ths_hot_reasons(date_str):
    url = f"http://zx.10jqka.com.cn/event/api/getharden/date/{date_str}/orderby/date/orderway/desc/charset/GBK/"
    try:
        r = EM_SESSION.get(url, headers={"User-Agent": UA}, timeout=10)
        return r.json().get("data") or []
    except Exception as e:
        print(f"[WARN] 同花顺热点失败: {e}")
        return []


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def analyze(zt, zb, dt, yzt, amount_rows, lhb, seats, inds, breadth, hot, date_str,
            cached=None):
    cached = cached or {}
    zt_n, zb_n, dt_n = len(zt), len(zb), len(dt)
    break_rate = round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0
    max_board = max((s["limit_days"] for s in zt), default=0)
    ladder = Counter(s["limit_days"] for s in zt)

    pcts = [s["pct"] for s in yzt if s.get("pct") is not None]
    yzt_stats = {
        "count": len(yzt),
        "avg_pct": round(sum(pcts) / len(pcts), 2) if pcts else 0,
        "up": sum(1 for p in pcts if p > 0),
        "advance": sum(1 for p in pcts if p >= 9.8),
        "big_loss": sum(1 for p in pcts if p <= -5),
        "flat": sum(1 for p in pcts if p == 0),
    }
    sorted_yzt = sorted(yzt, key=lambda s: s["pct"], reverse=True)
    yzt_stats["best"] = [(s["name"], s["pct"], s["zt_stat"]) for s in sorted_yzt[:5]]
    yzt_stats["worst"] = [(s["name"], s["pct"], s["zt_stat"]) for s in sorted_yzt[-8:]]
    broke = [s for s in yzt if s.get("pct") is not None and s["pct"] < 9.8]
    if broke:
        tb = max(broke, key=lambda s: ((s.get("y_limit_days") or 0), -s["pct"]))
        yzt_stats["top_break"] = (tb["name"], tb["pct"], tb.get("zt_stat", ""))
    else:
        yzt_stats["top_break"] = None

    ind_cnt = Counter(s["industry"] for s in zt)
    y_ind_cnt = Counter(s["industry"] for s in yzt)
    strong_sectors = []
    for ind, c in ind_cnt.most_common(20):
        if c >= 3:
            strong_sectors.append({"name": ind, "count": c,
                                   "members": [s["name"] for s in zt if s["industry"] == ind]})

    reason_tags = Counter()
    for s in hot:
        for t in str(s.get("reason", "")).split("+"):
            t = t.strip()
            if t:
                reason_tags[t] += 1

    if cached.get("amount_top"):
        amount_rows_fmt = cached["amount_top"]
    else:
        amount_rows_fmt = []
        for it in amount_rows:
            hi, lo = num(it.get("high")), num(it.get("low"))
            pos = (num(it.get("trade")) - lo) / (hi - lo) * 100 if hi > lo else 0
            amount_rows_fmt.append({
                "code": it.get("code"), "name": it.get("name"),
                "amount": num(it.get("amount")) / 1e8,
                "pct": num(it.get("changepercent")),
                "turnover": num(it.get("turnoverratio")),
                "pos": round(pos, 0),
            })

    by_code = {}
    for s in lhb:
        c = s["code"]
        if is_bond_code(c):
            continue
        if c not in by_code or abs(s["net_buy_wan"]) > abs(by_code[c]["net_buy_wan"]):
            by_code[c] = s
    lhb_list = sorted(by_code.values(), key=lambda s: s["net_buy_wan"], reverse=True)

    zb_sorted = sorted(zb, key=lambda s: s["amplitude"], reverse=True)
    drop_rows = cached.get("drops") if cached.get("drops") is not None else fetch_drop_rows(date_str)

    return {
        "date": date_str,
        "snapshot_cached": bool(cached),
        "zt_n": zt_n, "zb_n": zb_n, "dt_n": dt_n, "break_rate": break_rate,
        "max_board": max_board, "ladder": dict(sorted(ladder.items())),
        "yzt": yzt_stats,
        "strong_sectors": strong_sectors,
        "yesterday_sectors": y_ind_cnt.most_common(12),
        "reason_tags": dict(reason_tags.most_common(20)),
        "amount_top": amount_rows_fmt,
        "lhb_top": lhb_list[:8], "lhb_bottom": lhb_list[-5:],
        "inst_stocks": seats["inst_stocks"],
        "hot_seats": seats["hot_seats"],
        "zb_top": zb_sorted[:20], "dt": dt,
        "drops": drop_rows,
        "inds": inds, "breadth": breadth,
    }


def fetch_drop_rows(date_str):
    rows = []
    if not snapshot_is_target(date_str):
        return rows
    try:
        drops = fetch_sorted("changepercent", 1, 20)
        for it in drops:
            rows.append({"code": it.get("code"), "name": it.get("name"),
                         "pct": num(it.get("changepercent")),
                         "amount": num(it.get("amount")) / 1e8})
    except Exception as e:
        print(f"[WARN] 跌幅榜请求失败: {e}")
    return rows


def select_wind_vane(m):
    seen, out = set(), []

    def add(code, name, role, point):
        if code and code not in seen and len(out) < 8:
            seen.add(code)
            out.append({"code": code, "name": name, "role": role, "point": point})

    zt = sorted(m["zt"], key=lambda s: s["limit_days"], reverse=True)
    if zt:
        top = zt[0]
        add(top["code"], top["name"], f"{top['limit_days']}连板总龙头",
            "断板即情绪转弱信号")

    # 主线连板龙头：今日涨停数最多的行业里连板最高的个股
    if m["strong_sectors"]:
        main_ind = m["strong_sectors"][0]["name"]
        cands = [s for s in m["zt"] if s["industry"] == main_ind and s["limit_days"] >= 2]
        if cands:
            c = max(cands, key=lambda s: s["limit_days"])
            add(c["code"], c["name"], f"{main_ind}连板龙头", "主线高度")
        else:
            c = m["strong_sectors"][0]["members"][0]
            z = next((s for s in m["zt"] if s["name"] == c), None)
            if z:
                add(z["code"], z["name"], f"{main_ind}代表涨停", "主线扩散观察")

    if m["amount_top"]:
        a = m["amount_top"][0]
        add(a["code"], a["name"], "成交额龙头", "大盘资金风向")

    if m["lhb_top"]:
        b = m["lhb_top"][0]
        add(b["code"], b["name"], "龙虎榜净买最大", f"净买{b['net_buy_wan']/1e4:.1f}亿")

    if m["inst_stocks"]:
        ib = next((x for x in m["inst_stocks"] if x["inst_net_wan"] > 0), None)
        if ib:
            add(ib["code"], ib["name"], "机构净买方向",
                f"机构席位净买{ib['inst_net_wan']/1e4:.1f}亿")
        ise = next((x for x in reversed(m["inst_stocks"]) if x["inst_net_wan"] < 0), None)
        if ise:
            add(ise["code"], ise["name"], "高位派发负向",
                f"机构席位净卖{abs(ise['inst_net_wan'])/1e4:.1f}亿")

    if m["zb_top"]:
        z = max(m["zb_top"], key=lambda s: s["break_times"])
        add(z["code"], z["name"], "炸板极端分歧", f"炸板{z['break_times']}次")

    if m["yzt"]["best"]:
        best = m["yzt"]["best"][0]
        z = next((s for s in m["zt"] if s["name"] == best[0]), None)
        if z:
            add(z["code"], z["name"], "晋级标杆", f"昨涨停今日{best[1]:+.2f}%")

    if len(out) < 5 and m["lhb_top"]:
        for b in m["lhb_top"]:
            add(b["code"], b["name"], "龙虎榜资金", f"净买{b['net_buy_wan']/1e4:.1f}亿")
    if len(out) < 5 and m["amount_top"]:
        for a in m["amount_top"][1:]:
            add(a["code"], a["name"], "成交额前排", f"成交{a['amount']:.0f}亿")
    return out[:8]


CSS = """
:root{--bg:#f5f4ef;--paper:#fff;--ink:#1b1d20;--muted:#6c7078;--line:#e3e1d8;--dark:#181a1d;--dark-2:#23262b;--red:#d23b2e;--red-bg:#fbeae7;--green:#0c8a5e;--green-bg:#e6f3ec;--amber:#b7791f;--amber-bg:#f8efdc;--teal:#0f6f6a;--teal-bg:#e2efee;--maxw:1080px}
*{box-sizing:border-box;margin:0;padding:0}html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Segoe UI",sans-serif;font-size:14px;line-height:1.6;letter-spacing:0}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 20px}
.hero{background:var(--dark);color:#f2f1ec;padding:34px 0 30px}
.hero .kicker{font-size:12px;color:#aeb4ba}.hero h1{font-size:26px;font-weight:700;margin:8px 0 4px}
.hero .sub{font-size:13px;color:#aeb4ba}
.hero-stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-top:22px}
.hero-stat{background:var(--dark-2);border:1px solid #34383e;border-radius:8px;padding:10px 12px;min-width:0}
.hero-stat .label{font-size:11px;color:#9aa1a8}.hero-stat .value{font-size:19px;font-weight:700;margin-top:2px;white-space:nowrap}
.up{color:var(--red)}.down{color:var(--green)}.flat{color:var(--amber)}
main{padding:34px 0 8px}section{padding:26px 0 30px;border-bottom:1px solid var(--line)}section:last-of-type{border-bottom:0}
.sec-head{display:flex;align-items:baseline;gap:12px;margin-bottom:16px}.sec-num{font-size:12px;font-weight:700;color:var(--teal)}.sec-title{font-size:20px;font-weight:700}.sec-note{margin-left:auto;font-size:12px;color:var(--muted)}
.grid-4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.metric{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:14px 16px;min-width:0}
.metric .label{font-size:12px;color:var(--muted)}.metric .value{font-size:22px;font-weight:700;margin-top:3px}.metric .value small{font-size:12px;font-weight:400;color:var(--muted)}.metric .note{font-size:12px;color:var(--muted);margin-top:2px}
.callout{margin-top:14px;padding:12px 16px;border-radius:8px;background:var(--teal-bg);border-left:3px solid var(--teal);font-size:13px}
.callout.warn{background:var(--amber-bg);border-left-color:var(--amber)}.callout b{font-weight:700}
.index-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}
.index-box{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:12px 14px;min-width:0}
.index-box .name{font-size:12px;color:var(--muted)}.index-box .price{font-size:18px;font-weight:700;margin-top:2px}.index-box .chg{font-size:13px;font-weight:600}
.breadth{margin-top:14px;background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.breadth-row{display:flex;align-items:center;gap:14px;flex-wrap:wrap}.breadth-label{font-size:13px;color:var(--muted);min-width:76px}
.breadth-bar{flex:1 1 320px;height:14px;border-radius:7px;overflow:hidden;display:flex;background:#d9d7ce;min-width:220px}
.breadth-bar .b-up{background:var(--red)}.breadth-bar .b-flat{background:#b9b8b1}.breadth-bar .b-down{background:var(--green)}
.breadth-nums{font-size:13px}
.ladder{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:8px 16px 14px}
.ladder-row{display:grid;grid-template-columns:62px 1fr 180px;align-items:center;gap:12px;padding:8px 0;border-bottom:1px dashed var(--line)}.ladder-row:last-child{border-bottom:0}
.ladder-level{font-weight:700;font-size:14px}.ladder-level small{display:block;font-size:11px;color:var(--muted);font-weight:400}
.bar-track{height:18px;background:#efede6;border-radius:4px;overflow:hidden}.bar-fill{height:100%;background:var(--teal);border-radius:4px;min-width:3px}
.ladder-names{font-size:12px;color:var(--muted);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gap-note{margin-top:10px;font-size:13px;color:var(--amber)}
table{width:100%;border-collapse:collapse;background:var(--paper);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th{text-align:left;font-size:12px;color:var(--muted);font-weight:600;padding:9px 12px;background:#f0eee7;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px 12px;border-bottom:1px solid #efede6;font-size:13px;vertical-align:middle}tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right}td.center,th.center{text-align:center}
.table-scroll{overflow-x:auto}.tables-2{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
.mini-tbl-title{font-size:13px;font-weight:700;margin:4px 0 8px}.mini-tbl-title .sub{font-size:11px;color:var(--muted);font-weight:400;margin-left:6px}
.chip-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.chip{background:var(--paper);border:1px solid var(--line);border-radius:999px;padding:5px 12px;font-size:12px;color:var(--ink)}
.chip.red{background:var(--red-bg);border-color:#f0c9c3;color:#a52a20}.chip.green{background:var(--green-bg);border-color:#c3e2d2;color:#0a6b47}.chip.amber{background:var(--amber-bg);border-color:#e7d2a6;color:#8f5e15}.chip.teal{background:var(--teal-bg);border-color:#c3dddb;color:#0a5d59}
.tag-row{display:flex;flex-wrap:wrap;gap:6px}.tag{font-size:11px;background:#eeede7;color:#555a61;padding:3px 9px;border-radius:5px}
.two-col{display:grid;grid-template-columns:1.15fr .85fr;gap:16px;align-items:start}
.subhead{font-size:13px;font-weight:700;margin:16px 0 8px}.subhead:first-child{margin-top:0}
.wind-table td:first-child{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:12px;white-space:nowrap}
footer{padding:24px 0 40px}footer .note{font-size:12px;color:var(--muted)}footer .note+.note{margin-top:6px}
@media(max-width:900px){.hero-stats{grid-template-columns:repeat(3,minmax(0,1fr))}.grid-4{grid-template-columns:repeat(2,minmax(0,1fr))}.index-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.tables-2,.two-col{grid-template-columns:1fr}.ladder-row{grid-template-columns:56px 1fr}.ladder-names{grid-column:1/-1;text-align:left;white-space:normal}.sec-note{display:none}}
@media(max-width:560px){.hero h1{font-size:21px}.hero-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.wrap{padding:0 14px}td,th{padding:7px 8px}}
"""


def esc(v):
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_pct(v, digits=2, force=True):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    s = f"{f:+.{digits}f}%"
    if not force and f > 0:
        s = s.lstrip("+")
    return s


def fmt_pct_strip(v, digits=2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    s = f"{f:+.1f}" if f == int(f) else f"{f:+.{digits}f}"
    return s + "%"


def updown_cls(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "flat"
    return "up" if f > 0 else ("down" if f < 0 else "flat")


def build_html(m, indexes, wind):
    date_dash = f"{m['date'][:4]}-{m['date'][4:6]}-{m['date'][6:]}"
    live_ok = snapshot_is_target(m["date"])
    if live_ok and datetime.now().hour >= 15:
        data_note = f"全部为 {date_dash} 收盘后公开接口数据"
    elif live_ok:
        data_note = f"为 {date_dash} 盘中实时数据（未收盘，指数为实时值）"
    elif m.get("snapshot_cached"):
        data_note = (f"涨停池/龙虎榜/指数为 {date_dash} 数据；涨跌家数、成交额榜、"
                     "大跌榜来自目标日收盘快照缓存")
    else:
        data_note = (f"涨停池/龙虎榜/指数为 {date_dash} 数据；涨跌家数、成交额榜、"
                     "大跌榜为当前交易日实时快照，目标日期非当前交易日，已缺省")

    hero_stats = (
        f'<div class="hero-stat"><div class="label">涨停家数</div><div class="value up">{m["zt_n"]}</div></div>'
        f'<div class="hero-stat"><div class="label">炸板率</div><div class="value flat">{m["break_rate"]}%</div></div>'
        f'<div class="hero-stat"><div class="label">跌停家数</div><div class="value down">{m["dt_n"]}</div></div>'
        f'<div class="hero-stat"><div class="label">最高连板</div><div class="value">{m["max_board"]}板</div></div>'
        f'<div class="hero-stat"><div class="label">昨涨停晋级率</div><div class="value">{m["yzt"]["advance"]/max(m["yzt"]["count"],1)*100:.1f}%</div></div>'
        f'<div class="hero-stat"><div class="label">昨涨停平均溢价</div><div class="value up">{m["yzt"]["avg_pct"]:+.2f}%</div></div>'
    )

    index_items = ""
    for name, q in indexes.items():
        cls = updown_cls(q["change_pct"])
        arrow = "▲" if q["change_pct"] > 0 else ("▼" if q["change_pct"] < 0 else "—")
        index_items += (f'<div class="index-box"><div class="name">{esc(name)}</div>'
                        f'<div class="price">{q["price"]:.2f}</div>'
                        f'<div class="chg {cls}">{arrow} {q["change_pct"]:+.2f}%</div></div>')

    breadth_html = ""
    if m["breadth"]:
        b = m["breadth"]
        total = max(b["total"], 1)
        w_up = b["up"] / total * 100
        w_flat = b["flat"] / total * 100
        w_down = b["down"] / total * 100
        breadth_html = (
            '<div class="breadth"><div class="breadth-row">'
            '<div class="breadth-label">全市场涨跌</div>'
            f'<div class="breadth-bar"><div class="b-up" style="width:{w_up:.1f}%"></div>'
            f'<div class="b-flat" style="width:{w_flat:.1f}%"></div>'
            f'<div class="b-down" style="width:{w_down:.1f}%"></div></div>'
            f'<div class="breadth-nums"><span class="up">涨 {b["up"]}</span> · '
            f'<span class="flat">平 {b["flat"]}</span> · <span class="down">跌 {b["down"]}</span></div>'
            '</div></div>'
        )
    elif m["breadth"] is None:
        note = "涨跌家数实时快照暂不可用" if live_ok else "非当前交易日，实时快照缺省"
        breadth_html = ('<div class="breadth"><div class="breadth-row">'
                        '<div class="breadth-label">全市场涨跌</div>'
                        f'<div class="breadth-nums">{note}</div></div></div>')

    ladder_html = ""
    max_count = max(m["ladder"].values()) if m["ladder"] else 1
    boards = sorted([k for k in m["ladder"] if k >= 2], reverse=True)
    next_highest = boards[1] if len(boards) > 1 else None
    for days in boards:
        members = [s for s in m["zt"] if s["limit_days"] == days]
        if days == m["max_board"] and members:
            label = "总龙头"
            names = " · ".join(
                f'{s["name"]} · {s["zt_stat"]}' if s.get("zt_stat") else s["name"]
                for s in members)
        elif days == next_highest and members and max(x["break_times"] for x in members) >= 3:
            label = "板质偏弱"
            weak = max(members, key=lambda x: x["break_times"])
            names = " · ".join(s["name"] for s in members)
            names += f" · 炸板{weak['break_times']}次"
        else:
            label = f'{m["ladder"][days]} 只'
            names = " · ".join(s["name"] for s in members)
        width = m["ladder"][days] / max_count * 100
        ladder_html += (f'<div class="ladder-row"><div class="ladder-level">{days}板'
                        f'<small>{label}</small></div>'
                        f'<div class="bar-track"><div class="bar-fill" style="width:{width:.0f}%"></div></div>'
                        f'<div class="ladder-names">{esc(names)}</div></div>')
    first_count = m["ladder"].get(1, 0)
    width1 = first_count / max_count * 100 if max_count else 0
    ladder_html += (f'<div class="ladder-row"><div class="ladder-level">1板'
                    f'<small>{first_count} 只</small></div>'
                    f'<div class="bar-track"><div class="bar-fill" style="width:{width1:.0f}%"></div></div>'
                    f'<div class="ladder-names">低位首板为主</div></div>')

    top_board = max(m["zt"], key=lambda s: s["limit_days"]) if m["zt"] else None
    top_board_name = top_board["name"] if top_board else ""
    gap_parts = []
    present = {k for k in m["ladder"] if k >= 2}
    missing_range = ""
    if m["max_board"] > 5:
        missing = [d for d in range(6, m["max_board"]) if d not in present]
        if missing:
            missing_range = f"{min(missing)}-{max(missing)}板断层"
            gap_parts.append(f"{min(missing)}-{max(missing)} 板完全空缺，资金只敢抱最高标")
    ladder_note = f"{m['max_board']}板独木撑高度，{missing_range}" if missing_range else "高度与接力"
    tb = m["yzt"].get("top_break")
    if tb:
        tag = f"（{tb[2]}）" if tb[2] else ""
        gap_parts.append(f"昨日高度标{tb[0]}{tag}今日断板收 {tb[1]:+.2f}%")
    if tb and top_board_name:
        gap_parts.append(f"新高度已交接给{top_board_name}")
    gap_html = ""
    if gap_parts:
        gap_head = "；".join(gap_parts[:2])
        if len(gap_parts) > 2:
            gap_head += f"，{gap_parts[2]}"
        gap_html = (f'<div class="callout warn"><b>断层信号：</b>{gap_head}。'
                    '中位接力弱，追中高位要格外谨慎。</div>')

    sector_rows = ""
    for s in m["strong_sectors"]:
        sector_rows += (f'<tr><td>{esc(s["name"])}</td><td class="center up">{s["count"]}</td>'
                        f'<td>{esc(" · ".join(s["members"]))}</td></tr>')
    sector_table = ('<div class="table-scroll"><table><thead><tr><th>板块</th>'
                    '<th class="center">涨停数</th><th>涨停成员</th></tr></thead>'
                    f'<tbody>{sector_rows}</tbody></table></div>')

    y_inds = {name for name, _ in m["yesterday_sectors"] if name}
    t_inds = {s["name"] for s in m["strong_sectors"]}
    y_cnt = dict(m["yesterday_sectors"])
    t_cnt = {s["name"]: s["count"] for s in m["strong_sectors"]}
    extend = sorted(((n, y_cnt.get(n, 0), t_cnt[n]) for n in t_inds
                     if n in y_cnt and y_cnt.get(n, 0) >= 3), key=lambda x: -x[2])
    fresh = sorted(((n, y_cnt.get(n, 0), t_cnt[n]) for n in t_inds
                    if y_cnt.get(n, 0) < 3), key=lambda x: -x[2])
    fade = sorted(((n, y_cnt[n], t_cnt.get(n, 0)) for n in y_inds
                   if y_cnt[n] >= 3 and t_cnt.get(n, 0) < 3), key=lambda x: -x[1])
    chips = ""
    if extend:
        chips += '<span class="chip teal">延续扩散：' + \
            " · ".join(f"{esc(n)} {y}→{t}" for n, y, t in extend[:4]) + '</span>'
    if fresh:
        chips += '<span class="chip red">新爆发：' + \
            " · ".join(f"{esc(n)} {y}→{t}" for n, y, t in fresh[:4]) + '</span>'
    if fade:
        chips += '<span class="chip green">退潮：' + \
            " · ".join(f"{esc(n)} {y}→{t}" for n, y, t in fade[:4]) + '</span>'

    tags = "".join(f'<span class="tag">{esc(t)} {c}</span>' for t, c in list(m["reason_tags"].items())[:14])

    if m["amount_top"]:
        amt_rows = ""
        for a in m["amount_top"][:12]:
            amt_rows += (f'<tr><td>{esc(a["name"])}</td><td class="num">{a["amount"]:.1f}亿</td>'
                         f'<td class="num {updown_cls(a["pct"])}">{a["pct"]:+.2f}%</td>'
                         f'<td class="num">{a["pos"]:.0f}%</td></tr>')
        amt_table = ('<div class="table-scroll"><table><thead><tr><th>个股</th><th class="num">成交额</th>'
                     '<th class="num">涨跌幅</th><th class="num">收盘位置</th></tr></thead>'
                     f'<tbody>{amt_rows}</tbody></table></div>')
    else:
        note = "成交额榜实时快照暂不可用" if live_ok else "非当前交易日，实时榜缺省"
        amt_table = f'<div class="note">{note}</div>'

    lhb_buy = "".join(
        f'<tr><td>{esc(s["name"])}</td><td class="num up">+{s["net_buy_wan"]/1e4:.1f}亿</td>'
        f'<td class="num {updown_cls(s["change_pct"])}">{fmt_pct_strip(s["change_pct"])}</td></tr>'
        for s in m["lhb_top"][:6])
    lhb_sell = "".join(
        f'<tr><td>{esc(s["name"])}</td><td class="num down">-{abs(s["net_buy_wan"])/1e4:.1f}亿</td>'
        f'<td class="num {updown_cls(s["change_pct"])}">{fmt_pct_strip(s["change_pct"])}</td></tr>'
        for s in reversed(m["lhb_bottom"]))
    lhb_placeholder = '<tr><td colspan="3" class="note">龙虎榜数据尚未发布（收盘后晚间更新），暂缺</td></tr>'
    if not m["lhb_top"]:
        lhb_buy = lhb_placeholder
    if not m["lhb_bottom"]:
        lhb_sell = lhb_placeholder

    inst_buy_rows, inst_sell_rows = "", ""
    ib_list = [x for x in m["inst_stocks"] if x["inst_net_wan"] > 0][:5]
    is_list = [x for x in m["inst_stocks"] if x["inst_net_wan"] < 0][-6:][::-1]
    for x in ib_list:
        inst_buy_rows += (f'<tr><td>{esc(x["name"])}</td>'
                          f'<td class="num up">+{x["inst_net_wan"]/1e4:.1f}亿</td></tr>')
    for x in is_list:
        inst_sell_rows += (f'<tr><td>{esc(x["name"])}</td>'
                           f'<td class="num down">-{abs(x["inst_net_wan"])/1e4:.1f}亿</td></tr>')
    if not ib_list:
        inst_buy_rows = '<tr><td colspan="2" class="note">暂无</td></tr>'
    if not is_list:
        inst_sell_rows = '<tr><td colspan="2" class="note">暂无</td></tr>'

    hot_seats = "".join(f'<span class="tag">{esc(short_seat_name(seat))} {amt/1e8:.1f}亿</span>'
                        for seat, amt in list(m["hot_seats"].items())[:6])
    if not hot_seats:
        hot_seats = '<span class="chip">龙虎榜尚未发布，暂缺</span>'

    zb_rows = ""
    for s in m["zb_top"][:6]:
        zb_rows += (f'<tr><td>{esc(s["name"])}</td>'
                    f'<td class="num {updown_cls(s["pct"])}">{s["pct"]:+.2f}%</td>'
                    f'<td class="num">{s["amplitude"]:.1f}%</td>'
                    f'<td>{esc(s["industry"])} · 炸板{s["break_times"]}次</td></tr>')
    zb_table = ('<div class="table-scroll"><table><thead><tr><th>个股</th>'
                '<th class="num">收盘</th><th class="num">振幅</th><th>风险特征</th></tr></thead>'
                f'<tbody>{zb_rows}</tbody></table></div>')

    big_loss = "".join(f'<span class="chip green">{esc(n)} {p:+.2f}%</span>'
                       for n, p, _ in m["yzt"]["worst"][-6:])
    if m["drops"]:
        drops = "".join(f'<span class="chip green">{esc(d["name"])} {d["pct"]:+.2f}%</span>'
                        for d in m["drops"][:8])
    else:
        note = "大跌榜实时快照暂不可用" if live_ok else "非当前交易日，实时榜缺省"
        drops = f'<span class="chip">{note}</span>'

    wind_rows = ""
    for w in wind:
        wind_rows += (f'<tr><td>{esc(w["code"])}</td><td>{esc(w["name"])}</td>'
                      f'<td>{esc(w["role"])}</td><td>{esc(w["point"])}</td></tr>')
    wind_table = ('<div class="table-scroll"><table class="wind-table"><thead>'
                  '<tr><th>代码</th><th>名称</th><th>身份</th><th>观察点</th></tr></thead>'
                  f'<tbody>{wind_rows}</tbody></table></div>')

    yzt_c = m["yzt"]
    sentiment_note = ("容错率中等偏上，赚钱效应在收缩"
                      if m["zt_n"] < yzt_c["count"] else "容错率与赚钱效应")
    if m["zt_n"] < yzt_c["count"]:
        senti_concl = (
            f'昨日涨停 {yzt_c["count"]} 只中 {yzt_c["up"]} 只红盘，平均溢价 {yzt_c["avg_pct"]:+.2f}%，'
            f'打板容错尚可；但今日涨停家数从 {yzt_c["count"]} 收缩到 {m["zt_n"]}，'
            f'跌停仅 {m["dt_n"]} 家、炸板率 {m["break_rate"]}%，'
            '属于“有肉但热度在退”的中性偏多环境，不是全面亢奋期。')
    else:
        senti_concl = (
            f'昨日涨停 {yzt_c["count"]} 只中 {yzt_c["up"]} 只红盘，平均溢价 {yzt_c["avg_pct"]:+.2f}%，'
            f'晋级 {yzt_c["advance"]} 只、大亏 {yzt_c["big_loss"]} 只，炸板率 {m["break_rate"]}%、'
            f'跌停 {m["dt_n"]} 家，打板容错中等偏上。')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股短线复盘 · {date_dash}</title><style>{CSS}</style></head><body>
<header class="hero"><div class="wrap"><div class="kicker">A股短线复盘 · 盘后数据</div>
<h1>{date_dash} 情绪、梯队、板块与资金全景</h1>
<div class="sub">数据来源：东财涨停池/龙虎榜、腾讯指数、新浪行情 · 收盘后更新</div>
<div class="hero-stats">{hero_stats}</div></div></header>
<main class="wrap">
<section id="overview"><div class="sec-head"><span class="sec-num">00</span>
<span class="sec-title">盘面速览</span><span class="sec-note">指数与市场宽度</span></div>
<div class="index-grid">{index_items}</div>{breadth_html}</section>
<section id="sentiment"><div class="sec-head"><span class="sec-num">01</span>
<span class="sec-title">整体情绪</span><span class="sec-note">{sentiment_note}</span></div>
<div class="grid-4">
<div class="metric"><div class="label">涨停 / 炸板 / 跌停</div><div class="value">{m['zt_n']} <small>/ {m['zb_n']} / {m['dt_n']}</small></div><div class="note">炸板率 {m['break_rate']}%</div></div>
<div class="metric"><div class="label">昨日涨停今日表现</div><div class="value up">{m['yzt']['avg_pct']:+.2f}%</div><div class="note">{m['yzt']['count']} 只平均溢价</div></div>
<div class="metric"><div class="label">昨日涨停晋级</div><div class="value">{m['yzt']['advance']} <small>只</small></div><div class="note">晋级率 {m['yzt']['advance']/max(m['yzt']['count'],1)*100:.1f}%</div></div>
<div class="metric"><div class="label">昨日涨停大亏</div><div class="value down">{m['yzt']['big_loss']} <small>只</small></div><div class="note">收盘 ≤ -5%</div></div>
</div>
<div class="callout"><b>结论：</b>{senti_concl}</div></section>
<section id="ladder"><div class="sec-head"><span class="sec-num">02</span>
<span class="sec-title">连板梯队</span><span class="sec-note">{ladder_note}</span></div>
<div class="ladder">{ladder_html}</div>{gap_html}</section>
<section id="sectors"><div class="sec-head"><span class="sec-num">03</span>
<span class="sec-title">板块结构</span><span class="sec-note">3只以上涨停为强势板块</span></div>
{sector_table}
<div class="subhead">新旧对比</div><div class="chip-row">{chips}</div>
<div class="subhead">今日热点题材</div><div class="tag-row">{tags}</div></section>
<section id="capital"><div class="sec-head"><span class="sec-num">04</span>
<span class="sec-title">资金流向与龙虎榜</span><span class="sec-note">收盘位置 = 收在日内高低区间的位置</span></div>
<div class="subhead">成交额前排</div>{amt_table}
<div class="two-col"><div><div class="subhead">龙虎榜净买</div>
<div class="table-scroll"><table><thead><tr><th>个股</th><th class="num">净买</th><th class="num">涨跌幅</th></tr></thead><tbody>{lhb_buy}</tbody></table></div></div>
<div><div class="subhead">龙虎榜净卖</div>
<div class="table-scroll"><table><thead><tr><th>个股</th><th class="num">净卖</th><th class="num">涨跌幅</th></tr></thead><tbody>{lhb_sell}</tbody></table></div></div></div>
<div class="tables-2"><div><div class="mini-tbl-title">机构专用席位净买<span class="sub">方向判断</span></div>
<div class="table-scroll"><table><thead><tr><th>个股</th><th class="num">机构净买</th></tr></thead><tbody>{inst_buy_rows}</tbody></table></div></div>
<div><div class="mini-tbl-title">机构专用席位净卖<span class="sub">高位派发风险</span></div>
<div class="table-scroll"><table><thead><tr><th>个股</th><th class="num">机构净卖</th></tr></thead><tbody>{inst_sell_rows}</tbody></table></div></div></div>
<div class="subhead">游资活跃席位</div><div class="tag-row">{hot_seats}</div></section>
<section id="loss"><div class="sec-head"><span class="sec-num">05</span>
<span class="sec-title">亏钱效应</span><span class="sec-note">炸板与大幅回撤</span></div>
<div class="subhead">炸板观察</div>{zb_table}
<div class="subhead">昨日涨停今日大面</div><div class="chip-row">{big_loss}</div>
<div class="subhead">全市场大跌</div><div class="chip-row">{drops}</div></section>
<section id="watchlist"><div class="sec-head"><span class="sec-num">06</span>
<span class="sec-title">风向标自选</span><span class="sec-note">次日观察参考</span></div>
{wind_table}</section>
</main>
<footer class="wrap"><div class="note">数据来源：东方财富涨停池/炸板池/跌停池/昨涨停池、全市场龙虎榜与席位明细、腾讯财经指数、新浪行情成交额榜与涨跌家数，{data_note}。</div>
<div class="note">本报告为盘面复盘与情绪记录，不构成任何投资建议；短线交易风险极高，请独立决策。</div></footer>
</body></html>"""
    return html


def main():
    ap = argparse.ArgumentParser(description="A股每日短线复盘并生成HTML报告")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                    help="交易日 YYYYMMDD，默认今天")
    ap.add_argument("--out", default=None, help="输出目录，默认 outputs/（不存在则当前目录）")
    ap.add_argument("--name", default=None, help="HTML 文件名（不含扩展名），默认 A股短线复盘_YYYY-MM-DD")
    ap.add_argument("--no-theme", action="store_true", help="跳过同花顺热点题材抓取")
    args = ap.parse_args()

    date_str = args.date
    date_dash = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    print(f"开始复盘 {date_dash} ...")

    zt, zb, dt, yzt = fetch_zt_pools(date_str)
    if not zt:
        print("未获取到涨停池数据，可能为非交易日或数据未更新，请确认日期后重试。")
        sys.exit(2)

    inds = industry_comparison(10)
    lhb = daily_dragon_tiger(date_dash)
    seats = daily_seats(date_dash)
    name_map = {s["code"]: s["name"] for s in lhb}
    for x in seats["inst_stocks"]:
        if not x["name"]:
            x["name"] = name_map.get(x["code"], "")
    seats["inst_stocks"] = [x for x in seats["inst_stocks"]
                            if not is_bond_code(x["code"]) and "转债" not in x["name"]]
    hot = [] if args.no_theme else ths_hot_reasons(date_dash)

    live_ok = snapshot_is_target(date_str)
    out_dir = Path(args.out) if args.out else (Path("outputs") if Path("outputs").exists() else Path("."))
    out_dir.mkdir(parents=True, exist_ok=True)
    cached = None if live_ok else load_review_snapshot(out_dir, date_str)
    if live_ok:
        amount_rows = fetch_sorted("amount", 0, 20)
        breadth = market_breadth()
    elif cached:
        amount_rows, breadth = [], cached.get("breadth")
        print(f"[INFO] 使用 {date_dash} 收盘快照缓存补全涨跌家数/成交额榜/大跌榜")
    else:
        amount_rows, breadth = [], None
        print(f"[WARN] 当前实时快照属于 {current_session_date()}，与目标 {date_str} 不一致；"
              "涨跌家数/成交额榜/大跌榜缺省，指数按历史日K取数")

    m = analyze(zt, zb, dt, yzt, amount_rows, lhb, seats, inds, breadth, hot, date_str,
                cached)
    m["zt"], m["zb"] = zt, zb
    if live_ok:
        save_review_snapshot(out_dir, date_str, m)

    wind = select_wind_vane(m)
    indexes = tencent_quotes_for_date(
        ["sh000001", "sz399001", "sz399006", "sh000688", "sh000300"], date_str)

    html = build_html(m, indexes, wind)

    out_name = args.name or f"A股短线复盘_{date_dash}"
    if not out_name.endswith(".html"):
        out_name += ".html"
    out_path = out_dir / out_name
    out_path.write_text(html, encoding="utf-8")

    print(f"涨停 {m['zt_n']} / 炸板 {m['zb_n']}（{m['break_rate']}%）/ 跌停 {m['dt_n']}，最高 {m['max_board']} 板")
    print(f"昨涨停 {m['yzt']['count']} 只，平均溢价 {m['yzt']['avg_pct']:+.2f}%，晋级 {m['yzt']['advance']} 只，大亏 {m['yzt']['big_loss']} 只")
    if m["strong_sectors"]:
        print("强势板块:", ", ".join(s["name"] for s in m["strong_sectors"][:6]))
    print(f"报告已生成: {out_path.resolve()}")
    print(json.dumps({"date": date_dash, "zt": m["zt_n"], "zb": m["zb_n"],
                      "break_rate": m["break_rate"], "max_board": m["max_board"],
                      "yzt_avg": m["yzt"]["avg_pct"], "advance": m["yzt"]["advance"],
                      "report": str(out_path.resolve())}, ensure_ascii=False))
if __name__ == "__main__":
    main()
