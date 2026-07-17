# A 股每日股票分析系统

这个项目会每天抓取 A 股行情、最近一年 K 线和个股新闻，并从三方面打分：

- 成交量：当前成交量相对 60 日均量的放大程度。
- 成交额：当前成交额相对 60 日均额的放大程度。
- 市场消息：最近一年新闻标题和摘要的关键词情绪。

最终输出短线候选的 3 只股票。结果只是程序化筛选，不构成投资建议。

## 运行

每天 17:30 以后只需要运行这一条命令：

```bash
python3 main.py --once --full-scan --news-candidates 120 --news-per-stock 5 --paper-trade --paper-capital 20000
```

这条命令会生成当天股票筛选结果、2 万本金模拟盘操作计划、收益记录和策略调整记录。

注意：`python3 -m py_compile ...` 只是检查代码语法，不是每天运行模拟盘的命令。

生成某个月的模拟盘复盘：

```bash
python3 main.py --paper-summary --month 2026-07
```

如果不写 `--month`，默认生成当前月份复盘：

```bash
python3 main.py --paper-summary
```

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

启用 2 万本金纸面模拟投资：

```bash
python3 main.py --once --full-scan --news-candidates 120 --news-per-stock 5 --paper-trade --paper-capital 20000
```

建议在每个 A 股交易日 17:30 以后运行。这个时间点通常已经收盘，日 K、成交额和新闻数据更稳定。

运行逻辑：

- 当天 17:30 运行后，系统根据当天收盘数据生成“下一交易日模拟计划”。
- 第二个交易日 17:30 再运行时，系统会先用新的收盘价更新持仓收益，验证上一轮计划，再生成新的计划。
- 每次运行都会保存账户历史、操作流水和当前策略参数，后续筛选会读取这些记录调整模拟盘风险模式。

纸面模拟规则偏保守：

- 初始本金默认 20000 元，只做模拟记录，不会连接真实账户。
- 单票最多约 10000 元，最多同时持有 2 只，按 A 股 100 股一手计算。
- 极端弱势日不新开仓；例如样本平均跌幅较大、下跌占比过高或跌停样本较多。
- 候选股必须达到分数、趋势和风险过滤条件才模拟买入。
- 持仓触发 -8% 模拟止损、当日大跌、风险扣分升高或浮盈后转弱时模拟卖出。
- 每天会记录账户现金、持仓市值、累计收益、今日操作和原因。
- 如果最近记录连续表现较差，会自动提高开仓分数阈值、降低单票仓位和最大持仓数；如果连续表现较好，会小幅降低开仓分数阈值，但仍不突破最多 2 只持仓。

## 输出

- SQLite 缓存：`data/stock_analysis.sqlite3`
- 当日 CSV 报告：`data/recommendations_YYYY-MM-DD.csv`
- 模拟盘状态：`data/paper_trading/state.json`
- 模拟投资日报：`data/paper_trading/YYYY-MM-DD.md`
- 模拟账户历史：`data/paper_trading/account_history.json`
- 模拟操作流水：`data/paper_trading/operations.jsonl`
- 当前策略参数：`data/paper_trading/strategy.json`
- 策略调整历史：`data/paper_trading/strategy_history.json`
- 月度复盘报告：`data/paper_trading/summary_YYYY-MM.md`

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

系统会过滤 ST、退市股、北交所常见代码前缀，以及科创板常见 `688`、`689` 代码前缀。
