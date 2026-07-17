# A 股每日股票分析系统

这个项目会每天抓取 A 股行情、最近一年 K 线和个股新闻，并从三方面打分：

- 成交量：当前成交量相对 60 日均量的放大程度。
- 成交额：当前成交额相对 60 日均额的放大程度。
- 市场消息：最近一年新闻标题和摘要的关键词情绪。

最终输出短线候选的 3 只股票。结果只是程序化筛选，不构成投资建议。

## 运行

```bash
python3 main.py --once
```

推荐的正式运行方式：

```bash
python3 main.py --once --full-scan --news-candidates 120 --news-per-stock 5
```

这会尽量扫描全 A 股票池，先做资金、趋势、流动性和风险初筛，再对初筛靠前的 120 只抓消息面，最后推荐 3 只下周短线候选。

降低抓取量用于快速测试：

```bash
python3 main.py --once --max-candidates 10 --news-per-stock 3
```

`--max-candidates` 是参与分析的候选池数量，不是最终推荐数量。最终推荐数量由 `--top-n` 控制，默认返回 3 只。

准确率优先的全市场扫描：

```bash
python3 main.py --once --full-scan --news-candidates 120 --news-per-stock 5
```

`--full-scan` 会尽量拉取全 A 股票池，先对所有候选做资金、趋势、流动性和风险初筛，再对初筛靠前的 `--news-candidates` 只抓取消息面，最终输出 3 只。这样比只看成交额前 200 更全面，也比 4000 多只逐只抓新闻更稳。

打印真实请求 URL，便于复制到浏览器检查：

```bash
python3 main.py --once --max-candidates 1 --news-per-stock 1 --debug-urls
```

如果全市场行情列表接口在命令行环境被远端断开，可以先指定股票代码运行：

```bash
python3 main.py --once --codes 300308:中际旭创 --news-per-stock 3 --debug-urls
```

无网络或数据源临时不可用时，可以用本地样例数据验证完整流程：

```bash
python3 main.py --once --sample
```

每天 17:30 自动运行：

```bash
python3 main.py --daemon --hour 17 --minute 30
```

## 输出

- SQLite 缓存：`data/stock_analysis.sqlite3`
- 当日 CSV 报告：`data/recommendations_YYYY-MM-DD.csv`

## 数据源

当前实现使用东方财富公开接口：

- A 股实时行情列表
- 日 K 线
- 个股新闻搜索

如果东方财富 K 线接口在命令行环境被断开，系统会自动切到新浪日 K 线接口作为备用源。全市场行情列表当前仍优先使用东方财富；指定股票代码模式可以绕过全市场列表。

如果接口变更，可以只替换 `stock_analyzer/fetchers.py`，分析逻辑不需要改。

注意：`https://push2his.eastmoney.com/api/qt/stock/kline/get` 这个裸地址不会返回 K 线数据，必须带完整查询参数，例如 `secid=1.600519&klt=101&fqt=1&beg=20250717&end=20260717&fields1=...&fields2=...`。

## 评分说明

总分由以下部分组成，更偏向“下周短线候选”：

- 资金活跃度：成交量和成交额相对 60 日均值，权重 35%
- 趋势结构：5/10/20/60 日均线、近 5 日和 20 日动量、60 日位置，权重 25%
- 市场消息：新闻和股吧资讯关键词情绪，权重 20%
- 流动性：成交额和换手率，权重 15%
- 风险扣分：涨停追高、大跌破位、20 日高位回撤、监管/减持/业绩风险关键词

系统会过滤 ST、退市股和北交所常见代码前缀。
