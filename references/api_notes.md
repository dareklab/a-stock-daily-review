# 数据接口与字段说明

脚本只依赖 `requests`，不依赖 pandas。所有东财请求经 `em_get()` 串行限流（默认间隔 1s+ 抖动），批量场景可把 `EM_MIN_INTERVAL` 调大。

## 东财 push2ex（涨停/炸板/跌停/昨涨停池）

端点：`https://push2ex.eastmoney.com/{getTopicZTPool|getTopicZBPool|getTopicDTPool|getYesterdayZTPool}`

参数：`ut=7eea3edcaed734bea9cbfc24409ed989`、`dpt=wz.ztzt`、`date=YYYYMMDD`。

关键字段（原始值注意单位）：
- `c` 代码、`n` 名称、`p` 价格（×1000）、`zdp` 涨跌幅、`lbc` 连板数、`fbt/lbt` 首封/末封时间（HHMMSS）、`fund` 封板资金（元）、`zbc` 炸板次数、`hybk` 行业、`zttj` N天M板、`zf` 振幅、`zs` 涨速、`days` 连续跌停、`oc` 开板次数。
- 昨涨停池 `getYesterdayZTPool`：`ylbc` 昨连板数、`yfbt` 昨首封时间、`zdp` 今日涨幅，用于计算昨涨停溢价/晋级率/大面。

## 东财数据中心（龙虎榜与席位）

统一端点：`https://datacenter-web.eastmoney.com/api/data/v1/get`

- 全市场龙虎榜：`RPT_DAILYBILLBOARD_DETAILSNEW`，过滤 `TRADE_DATE='YYYY-MM-DD'`；`BILLBOARD_NET_AMT` 净买额（元）。
- 买入席位：`RPT_BILLBOARD_DAILYDETAILSBUY`；卖出席位：`RPT_BILLBOARD_DAILYDETAILSSELL`。
- 席位字段：`OPERATEDEPT_NAME` 营业部、`OPERATEDEPT_CODE`（`"0"` 为机构专用席位）、`BUY/SELL/NET`（元）。
- 东财分页按 `pageNumber/pageSize` 走，脚本已自动翻页。

## 东财 push2（行业板块）

端点：`https://push2.eastmoney.com/api/qt/clist/get`，`fs=m:90+t:2`，字段 `f3/f12/f14/f104/f105/f128/f136/f140`。

注意：该 clist 接口对部分 IP 有间歇性连接风控（`RemoteDisconnected`），成交额榜/跌幅榜/涨跌家数已改用新浪源兜底，不要重复并发请求。

## 腾讯财经（指数）

端点：`https://qt.gtimg.cn/q=`，GBK 编码，`~` 分隔。指数必须显式前缀：`sh000001`（上证）、`sz399001`（深成指）、`sz399006`（创业板指）、`sh000688`（科创50）、`sh000300`（沪深300）。

字段索引：3 现价、32 涨跌幅、33 最高、34 最低、37 成交额（万）、38 换手率。

历史日K：`https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={secid},day,,,60,qfq`，返回 `data.{secid}.day` 数组 `[日期,开,收,高,低,量,额(元),振幅]`。脚本对非当前交易日按此取历史收盘并计算涨跌幅，避免把“今天实时指数”标成目标日期。

## 新浪行情（成交额榜/跌幅榜/涨跌家数）

端点：
- 行情分页：`https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData`
- 家数：`Market_Center.getHQNodeStockCount`

参数：`node=hs_a`、`sort=amount|changepercent`、`asc=0|1`、`num=100`。字段：`code/name/trade/changepercent/amount/high/low/turnoverratio`。

涨跌家数用涨幅降序二分法统计，覆盖沪深 A 股（不含北交所）。

注意：新浪行情只有当前交易日的实时快照，没有历史成交额榜/涨跌家数。脚本通过腾讯快照的时间戳判断“当前快照属于哪个交易日”；当天运行时会写入 `review_snapshot_YYYYMMDD.json`，跨交易日复盘时优先读取该缓存补全，无缓存才缺省并在报告中标注，不写入错误日期数据。

## 同花顺热点（题材标签，可选）

端点：`http://zx.10jqka.com.cn/event/api/getharden/date/YYYY-MM-DD/orderby/date/orderway/desc/charset/GBK/`

字段 `reason` 为人工运营的题材标签（如“光模块+CPO”），按 `+` 拆分统计热度。失败时静默降级为空，不影响报告生成。

## 常见问题

- 非交易日/盘前：涨停池返回空，脚本退出并提示。
- 东财风控：403/429/连接被重置时，间隔几秒重试或换网络；不要高频重试。
- 字段单位：封板资金/龙虎榜净买额为元；新浪成交额为元；腾讯成交额为万元。
- 龙虎榜展示：接口净买额先换算成万元（`/1e4`），HTML 再换算成亿元（`/1e4`）展示；机构席位净买/游资席位同理。
- 可转债（代码以 11/12/13 开头，含名称仅带“转”字的新上市转债）已从龙虎榜与机构席位统计中过滤，避免以名称判断漏网。
- 游资活跃席位过滤“自然人/其他自然人/机构投资者”聚合席位，仅保留具体营业部与沪深股通专用席位；席位名做简写（如“瑞银花园石桥”）。
- 龙虎榜净卖按净卖金额从大到小展示；机构专用席位净卖固定展示 6 条。
- 报告生成：HTML 为自包含单文件，中文文件名直接打开即可，无需服务器。
