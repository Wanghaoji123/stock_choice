# A 股每日股票分析系统

这个项目会每天抓取 A 股行情、最近一年 K 线和个股新闻，并从三方面打分：

- 成交量：当前成交量相对 60 日均量的放大程度。
- 成交额：当前成交额相对 60 日均额的放大程度。
- 市场消息：最近一年新闻标题和摘要的关键词情绪。
- **资金合力（第一条件）**：只认可两种组合——超大单与大单同向净流入，或超大单与中单同向净流入。小单不参与确认；超大单净卖出而其他单量承接的形态会被重罚并剔除。

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
- 下一交易日 09:35、10:30、14:50 由轻量任务只检查待执行股票；价位、量能和当日资金方向同时确认后才模拟成交。
- 14:50 仍未满足条件的订单当日失效；17:30 收盘任务更新持仓收益并生成新计划，不再事后按开盘价补记买入。
- 每次运行都会保存账户历史、操作流水和当前策略参数，后续筛选会读取这些记录调整模拟盘风险模式。

纸面模拟规则（2026-08 起采用可成交口径）：

- 初始本金默认 20000 元，只做模拟记录，不会连接真实账户。
- 单票最多约 10000 元，最多同时持有 2 只，按 A 股 100 股一手计算。
- 市场会按强弱分层：强市只提高确认要求，不放松追高；弱市保留小仓试仓，极端弱势也尽量不再彻底空仓。
- 最终只推荐资金合力最强的 3 只；先按资金合力排序，但综合分仍必须达到当前策略开仓线。
- 开仓以资金合力为前提，再检查上涨趋势与 20 日压力位；当前口径已从“只认非常强的资金合力”放宽到“中等偏强合力即可进入观察”，避免天天筛到空仓，但明显派发结构仍会被剔除。
- 策略允许靠近 20 日线试仓，但近 5 日急涨且逼近压力位时仍等待突破确认或回踩。
- T 日收盘后只创建信号订单，不使用事后已知的 T 日收盘价成交，也不再于次日收盘后倒推开盘成交。
- 当前策略版本为 `v3-intraday-confirmation`：T+1 仅在进入低吸区，或放量突破确认价且资金合力未转弱时成交，成交价使用检查时实时价并计入滑点。
- 资金分组结构化保存为 `A+B`、`A_ONLY`、`B_ONLY`、`C`，并读取 SQLite 最近 5 个记录日衡量合力持续性。
- 最终候选增加数据质量门控；关键行情、资金流或 K 线缺失时不创建正式订单。
- 模拟费用包含万三佣金（最低 5 元）和卖出万五印花税；这些是可调整的建模参数，不代表券商实际收费。
- 个股触发 -5% 止损、+20% 止盈；浮盈达到 10% 后把保护线提高到成本附近；最长持有 60 个交易日。
- 账户从历史峰值回撤达到 10% 后，新单不超过 5000 元；达到 15% 后锁定新开仓，需人工复盘后解除。
- `buy_score` 是实际开仓条件，不再只是日报中显示但不生效的参数。
- 每天会记录账户现金、持仓市值、累计收益、今日操作和原因。
- 如果最近记录连续表现较差，会自动提高开仓分数阈值、降低单票仓位和最大持仓数；如果连续表现较好，会小幅降低开仓分数阈值，但仍不突破最多 2 只持仓。
- 日报会基于日 K 生成次日价位计划，包括低吸区、确认买点、止损位和止盈参考。这个计划用于次日盘中观察，不是分钟 K 级别的精确择时。

## 输出

- SQLite 缓存：`data/stock_analysis.sqlite3`
- 当日 CSV 报告：`data/recommendations_YYYY-MM-DD.csv`
- 模拟盘状态：`data/paper_trading/state.json`
- 模拟投资日报：`data/paper_trading/YYYY-MM-DD.md`
- 日 K 次日价位计划：`data/paper_trading/timing_plan_YYYY-MM-DD.json`
- 日 K 价位计划历史：`data/paper_trading/timing_plan_history.json`
- 模拟账户历史：`data/paper_trading/account_history.json`
- 模拟操作流水：`data/paper_trading/operations.jsonl`
- 当前策略参数：`data/paper_trading/strategy.json`
- 前向信号效果：`data/paper_trading/signal_observations.json`（记录 A/B/C 组的 T+1、T+3、T+5、T+10、T+20 收益）
- 分组统计摘要：`data/paper_trading/signal_summary.md`
- 策略调整历史：`data/paper_trading/strategy_history.json`
- 月度复盘报告：`data/paper_trading/summary_YYYY-MM.md`

## 数据源

当前实现使用东方财富公开接口：

- A 股实时行情列表
- 日 K 线
- 当日分单资金流（日资金流接口，必须校验返回日期为当天）
- 个股新闻搜索

如果东方财富 K 线接口在命令行环境被断开，系统会自动切到新浪日 K 线接口作为备用源。全市场行情列表当前仍优先使用东方财富；指定股票代码模式可以绕过全市场列表。

如果接口变更，可以只替换 `stock_analyzer/fetchers.py`，分析逻辑不需要改。

注意：`https://push2his.eastmoney.com/api/qt/stock/kline/get` 这个裸地址不会返回 K 线数据，必须带完整查询参数，例如 `secid=1.600519&klt=101&fqt=1&beg=20250717&end=20260717&fields1=...&fields2=...`。

## 评分说明

总分由以下部分组成，更偏向“下周短线候选”：

- 资金合力：超大单+大单或超大单+中单的同向净流入、分时均价线位置、冲高回落，作为**第一准入条件**；通过后按合力强度优先排序
- 资金活跃度：成交量和成交额相对 60 日均值，权重 22%
- 趋势结构：5/10/20/60 日均线、近 5 日和 20 日动量、60 日位置，权重 16%
- 市场消息：新闻和股吧资讯关键词情绪，权重 12%
- 流动性：成交额和换手率，权重 8%
- 派发扣分：中小单承接而超大/大单净卖出、冲高回落且未守住均价线
- 风险扣分：涨停追高、大跌破位、20 日高位回撤、监管/减持/业绩风险关键词

默认采用偏保守但不极端的模式：未获取到分单资金时仍会保守处理；资金合力分低于 58 分，或出现明显派发扣分的股票，才会被直接剔除。数据源临时不可用但需要查看旧版候选时，可显式加 `--allow-missing-capital-flow`，该模式不应作为正式选股依据。

系统会过滤 ST、退市股、北交所常见 `8`、`4`、`92` 代码前缀，以及科创板常见 `688`、`689` 代码前缀。

全市场扫描结束会打印资金流接口的成功、空数据、异常和日期过期数量。请求数不少于20时，如果成功率低于50%，任务会失败并触发告警，不再把数据源故障误报为“没有候选股票”。

全市场资金流优先随行情分页批量取得，不再对4000多只股票逐只请求资金流接口；只有可能进入A/B组的少量股票才继续获取分时数据。原始分单资金按股票代码和交易日期保存在 SQLite 的 `capital_flows` 表，便于后续审计。

## 本地自动运行

项目提供 `systemd --user` 模板，无需服务器，但计划时间电脑必须开机；`Persistent=true` 会在当天稍晚开机后补跑。安装方式：

```bash
mkdir -p ~/.config/systemd/user
cp systemd/stock-choice.service systemd/stock-choice.timer ~/.config/systemd/user/
cp systemd/stock-choice-intraday-dispatch.service systemd/stock-choice-intraday.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now stock-choice.timer stock-choice-intraday.timer
systemctl --user list-timers stock-choice.timer stock-choice-intraday.timer
```

收盘任务在工作日 17:30 运行 `scripts/run_daily.sh`；盘中任务在 09:35、10:30、14:50 运行 `scripts/run_intraday.sh`。日志保存在 `data/logs/`，失败记录保存在 `data/alerts/`；图形桌面可用时还会调用 `notify-send`。节假日或行情日期不是当天时不会生成虚假的模拟成交。

模板目前写入了本机项目绝对路径 `/home/wanghaoji/stock_choice`；项目移动后需同步修改 service 和脚本。系统只按工作日调度，不自带完整交易所日历，因此是否交易日最终以当日 K 线日期校验为准。

## 信号实验

- A+B 组：超大单、大单和中单同时净流入。
- A_ONLY 组：超大单与大单净流入，中单未同时确认。
- B_ONLY 组：超大单与中单净流入，大单未同时确认。
- C 组：没有上述资金合力，但综合趋势等条件靠前；只观察，不下单。

正式模拟订单仍只来自严格资金合力候选。三组从现在开始做前向统计，不会用当前结果伪造过去的资金流历史。样本不足 100 个信号前，不建议据此自动修改核心权重。
