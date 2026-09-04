from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

from .analyzer import build_recommendations
from .config import Settings
from .fetchers import EastMoneyClient
from .models import CapitalFlow, IntradayPoint, KLine, NewsItem, StockQuote
from .paper_trading import execute_pending_session, generate_monthly_summary, load_state, run_paper_trading
from .sample_data import sample_klines, sample_news, sample_quotes
from .storage import Storage


def run_once(settings: Settings) -> int:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    client = EastMoneyClient(settings)
    storage = Storage(settings.db_path)
    flow_history_by_code: dict[str, list[CapitalFlow]] = {}
    try:
        if settings.codes:
            quotes, klines_by_code, news_by_code = fetch_explicit_codes(client, storage, settings)
            flows_by_code, intraday_by_code = fetch_market_structure(
                client, quotes, allow_individual_fallback=True
            )
            storage.save_capital_flows(datetime.now().strftime("%Y-%m-%d"), flows_by_code.values())
            flow_history_by_code = storage.load_recent_capital_flows(flows_by_code.keys())
            picks = build_recommendations(
                quotes,
                klines_by_code,
                news_by_code,
                settings.top_n,
                flows_by_code,
                intraday_by_code,
                require_capital_cohesion=not settings.allow_missing_capital_flow,
                flow_history_by_code=flow_history_by_code,
            )
            observation_picks = build_observation_candidates(
                picks, quotes, klines_by_code, news_by_code, flows_by_code, intraday_by_code
            )
            run_date = datetime.now().strftime("%Y-%m-%d")
            storage.save_recommendations(run_date, picks)
            write_report(settings.data_dir / f"recommendations_{run_date}.csv", picks)
            if settings.paper_trade and market_data_is_current(klines_by_code, run_date):
                paper_path = run_paper_trading(
                    settings.data_dir,
                    run_date,
                    picks,
                    quotes,
                    settings.paper_capital,
                    klines_by_code,
                    observation_picks,
                )
                print(f"模拟投资日报已保存: {paper_path}")
            elif settings.paper_trade:
                print("模拟盘未运行：K线最后交易日不是今天，可能尚未收盘或今天不是交易日。")
            print_report(picks)
            print(f"\n报告已保存: {settings.data_dir / f'recommendations_{run_date}.csv'}")
            print("提示：这是量化和新闻情绪筛选结果，不构成投资建议；买卖前请人工复核公告、财报和风险。")
            return 0

        if settings.use_sample_data:
            print("正在使用本地样例数据...")
            quotes = sample_quotes()[: settings.max_candidates]
        else:
            print("正在拉取 A 股行情...")
            quotes = client.fetch_a_share_quotes()
        storage.save_quotes(quotes)
        # 完整行情必须一直保留给模拟成交、持仓估值和大盘判断使用。
        # quotes 后续会缩成资金流预筛名单，只用于选股，不能再传给账户模块。
        market_quotes = list(quotes)

        if not settings.use_sample_data:
            client.fetch_qq_capital_flows([quote.code for quote in quotes])
            quote_codes = {quote.code for quote in quotes}
            coverage = len(quote_codes & client.bulk_capital_flows.keys()) / len(quotes) if quotes else 0.0
            if coverage < 0.5:
                raise RuntimeError(f"腾讯资金流与行情匹配率仅 {coverage:.1%}，任务已停止")
            quotes = preselect_structure_quotes(quotes, client.bulk_capital_flows, settings.structure_candidates)
            print(f"资金流预筛完成：只对 {len(quotes)} 只候选抓取K线，不再逐只扫描全市场。")

        klines_by_code: dict[str, list[KLine]] = {}
        news_by_code: dict[str, list[NewsItem]] = {}
        for idx, quote in enumerate(quotes, start=1):
            print(f"[{idx}/{len(quotes)}] 抓取K线 {quote.code} {quote.name}")
            if settings.use_sample_data:
                klines = sample_klines(quote.code, quote.price or 10.0)
                news = sample_news(quote.code, quote.name)
            else:
                try:
                    klines = client.fetch_kline(quote.code)
                except RuntimeError as exc:
                    print(f"  跳过：{exc}")
                    continue
            klines_by_code[quote.code] = klines
            storage.save_klines(klines)
            if settings.use_sample_data:
                news_by_code[quote.code] = news
                storage.save_news(news)
            time.sleep(0.03)

        if not settings.use_sample_data:
            run_date = datetime.now().strftime("%Y-%m-%d")
            if not market_data_is_current(klines_by_code, run_date):
                raise RuntimeError("K线最后交易日不是今天，无法确认腾讯资金流属于今天，任务已停止")
            shortlist_size = min(len(quotes), max(settings.top_n, settings.news_candidates))
            print(f"第一阶段完成：已对 {len(klines_by_code)} 只股票做资金/趋势/流动性初筛。")
            print(f"第二阶段：对初筛前 {shortlist_size} 只抓取消息面，再输出最终 {settings.top_n} 只。")
            quote_by_code = {quote.code: quote for quote in quotes}
            bulk_coverage = len(client.bulk_capital_flows) / len(quotes) if quotes else 0.0
            if bulk_coverage >= 0.5:
                # 东方财富行情批量返回了分单资金，可以直接覆盖全市场。
                structure_quotes = quotes
                allow_individual_fallback = False
                print(f"全市场批量资金流覆盖率 {bulk_coverage:.1%}，直接进行资金合力复核。")
            else:
                # 行情已切到新浪等备用源时没有分单字段：先用量价趋势预筛，
                # 再对少量股票逐只获取资金流，避免请求4000多次触发限流。
                prefilter_size = min(len(quotes), max(60, settings.news_candidates))
                prefiltered = build_recommendations(
                    quotes,
                    klines_by_code,
                    {},
                    prefilter_size,
                    {},
                    {},
                    require_capital_cohesion=False,
                )
                quote_by_code = {quote.code: quote for quote in quotes}
                structure_quotes = [quote_by_code[item.code] for item in prefiltered]
                allow_individual_fallback = True
                print(
                    f"批量资金流覆盖率仅 {bulk_coverage:.1%}，"
                    f"先按量价趋势预筛 {len(structure_quotes)} 只，再逐只复核资金流。"
                )
            print(f"资金合力复核：对全部 {len(structure_quotes)} 只初筛样本抓取分单资金与分时结构。")
            flows_by_code, intraday_by_code = fetch_market_structure(
                client,
                structure_quotes,
                allow_individual_fallback=allow_individual_fallback,
            )
            # 腾讯已批量取得全市场数据，全部落库用于后续前向统计；
            # flows_by_code 只是进入本轮精细复核的子集。
            storage.save_capital_flows(
                datetime.now().strftime("%Y-%m-%d"), client.bulk_capital_flows.values()
            )
            flow_history_by_code = storage.load_recent_capital_flows(client.bulk_capital_flows.keys())
            preliminary = build_recommendations(
                structure_quotes,
                klines_by_code,
                {},
                shortlist_size,
                flows_by_code,
                intraday_by_code,
                require_capital_cohesion=not settings.allow_missing_capital_flow,
                flow_history_by_code=flow_history_by_code,
            )
            for idx, item in enumerate(preliminary, start=1):
                quote = quote_by_code[item.code]
                print(f"[{idx}/{len(preliminary)}] 抓取消息 {quote.code} {quote.name}")
                try:
                    news = client.fetch_news(quote.code, quote.name)
                except RuntimeError as exc:
                    print(f"  消息面跳过：{exc}")
                    news = []
                news_by_code[quote.code] = news
                storage.save_news(news)
                time.sleep(0.2)
            final_quotes = [quote_by_code[item.code] for item in preliminary]
        else:
            final_quotes = quotes

        picks = build_recommendations(
            final_quotes,
            klines_by_code,
            news_by_code,
            settings.top_n,
            flows_by_code if not settings.use_sample_data else {},
            intraday_by_code if not settings.use_sample_data else {},
            require_capital_cohesion=not settings.use_sample_data and not settings.allow_missing_capital_flow,
            flow_history_by_code=flow_history_by_code,
        )
        observation_picks = picks if settings.use_sample_data else build_observation_candidates(
            picks, quotes, klines_by_code, news_by_code, flows_by_code, intraday_by_code
        )
        run_date = datetime.now().strftime("%Y-%m-%d")
        storage.save_recommendations(run_date, picks)
        write_report(settings.data_dir / f"recommendations_{run_date}.csv", picks)
        if settings.paper_trade and market_data_is_current(klines_by_code, run_date):
            paper_path = run_paper_trading(
                settings.data_dir,
                run_date,
                picks,
                market_quotes,
                settings.paper_capital,
                klines_by_code,
                observation_picks,
            )
            print(f"模拟投资日报已保存: {paper_path}")
        elif settings.paper_trade:
            print("模拟盘未运行：K线最后交易日不是今天，可能尚未收盘或今天不是交易日。")
        print_report(picks)
        print(f"\n报告已保存: {settings.data_dir / f'recommendations_{run_date}.csv'}")
        print("提示：这是量化和新闻情绪筛选结果，不构成投资建议；买卖前请人工复核公告、财报和风险。")
        return 0
    finally:
        storage.close()


def run_intraday_execution(settings: Settings, session_time: str) -> int:
    state = load_state(settings.data_dir / "paper_trading" / "state.json", settings.paper_capital)
    pending = [row for row in state.pending_orders if str(row.get("signal_date") or "") < datetime.now().strftime("%Y-%m-%d")]
    if not pending:
        print("当前没有上一交易日待执行订单。")
        return 0
    client = EastMoneyClient(settings)
    quotes: list[StockQuote] = []
    codes = [str(row.get("code") or "") for row in pending if row.get("code")]
    names = {str(row.get("code")): str(row.get("name") or row.get("code")) for row in pending}
    storage = Storage(settings.db_path)
    try:
        klines_by_code = storage.load_recent_klines(codes)
    finally:
        storage.close()
    for code in codes:
        try:
            quote = client.fetch_quote(code, names[code])
            if quote and quote.fetched_at.date().isoformat() == datetime.now().strftime("%Y-%m-%d"):
                quotes.append(quote)
        except RuntimeError as exc:
            print(f"{code} 盘中行情暂不可用：{exc}")
    try:
        flows = client.fetch_qq_capital_flows(codes)
    except RuntimeError as exc:
        print(f"盘中资金流暂不可用：{exc}")
        flows = {}
    if not quotes:
        print("没有取得日期为今天的实时行情，可能休市或数据源尚未更新；保留全部订单。")
        return 0
    report, actions = execute_pending_session(
        settings.data_dir, datetime.now().strftime("%Y-%m-%d"), quotes, flows,
        klines_by_code, settings.paper_capital, session_time,
    )
    for action in actions:
        print(f"{action['action']} {action.get('code', '')}：{action['reason']}")
    print(f"盘中执行报告已保存: {report}")
    return 0


def parse_code_item(item: str) -> tuple[str, str]:
    if ":" in item:
        code, name = item.split(":", 1)
        return code.strip(), name.strip()
    return item.strip(), item.strip()


def market_data_is_current(klines_by_code: dict[str, list[KLine]], run_date: str) -> bool:
    latest_dates = [rows[-1].trade_date.isoformat() for rows in klines_by_code.values() if rows]
    return bool(latest_dates) and max(latest_dates) == run_date


def preselect_structure_quotes(
    quotes: list[StockQuote],
    flows_by_code: dict[str, CapitalFlow],
    limit: int,
) -> list[StockQuote]:
    """优先保留 A/B 合力股票，并留出少量 C 组观察样本。"""
    cohesive: list[StockQuote] = []
    observation: list[StockQuote] = []
    for quote in quotes:
        flow = flows_by_code.get(quote.code)
        if flow is None:
            continue
        if (flow.extra_large_net or 0) > 0 and (
            (flow.large_net or 0) > 0 or (flow.medium_net or 0) > 0
        ):
            cohesive.append(quote)
        else:
            observation.append(quote)

    def strength(quote: StockQuote) -> tuple[float, float]:
        flow = flows_by_code[quote.code]
        combined = (flow.extra_large_net or 0) + max(flow.large_net or 0, flow.medium_net or 0)
        ratio = combined / max(quote.amount or 0, 1)
        return ratio, quote.amount or 0

    cohesive.sort(key=strength, reverse=True)
    observation.sort(key=lambda quote: quote.amount or 0, reverse=True)
    observation_slots = min(20, max(5, limit // 10))
    selected = cohesive[: max(0, limit - observation_slots)] + observation[:observation_slots]
    return selected[:limit]


def build_observation_candidates(
    strict_picks,
    quotes,
    klines_by_code,
    news_by_code,
    flows_by_code,
    intraday_by_code,
):
    """保留正式合力候选，并加入最多10个无合力观察样本（不用于下单）。"""
    exploratory = build_recommendations(
        quotes,
        klines_by_code,
        news_by_code,
        50,
        flows_by_code,
        intraday_by_code,
        require_capital_cohesion=False,
    )
    result = list(strict_picks)
    known = {item.code for item in result}
    for item in exploratory:
        if item.code in known or item.capital_cohesion_score >= 58.0:
            continue
        result.append(item)
        known.add(item.code)
        if sum(1 for row in result if row.capital_cohesion_score < 58.0) >= 10:
            break
    return result


def fetch_explicit_codes(
    client: EastMoneyClient,
    storage: Storage,
    settings: Settings,
) -> tuple[list[StockQuote], dict[str, list[KLine]], dict[str, list[NewsItem]]]:
    print("正在按指定股票代码分析...")
    quotes: list[StockQuote] = []
    klines_by_code: dict[str, list[KLine]] = {}
    news_by_code: dict[str, list[NewsItem]] = {}
    for idx, raw_item in enumerate(settings.codes, start=1):
        code, name = parse_code_item(raw_item)
        if not code:
            continue
        print(f"[{idx}/{len(settings.codes)}] 分析 {code} {name}")
        try:
            klines = client.fetch_kline(code)
            news = client.fetch_news(code, name)
        except RuntimeError as exc:
            print(f"  跳过：{exc}")
            continue
        if not klines:
            print("  跳过：未抓取到 K 线数据")
            continue
        latest = klines[-1]
        quote = client.fetch_quote(code, name)
        if quote is None:
            quote = StockQuote(
                code=code,
                name=name,
                market="sh" if code.startswith(("5", "6", "9")) else "sz",
                price=latest.close,
                pct_chg=latest.pct_chg,
                volume=latest.volume,
                amount=latest.amount,
                turnover_rate=latest.turnover_rate,
                market_cap=None,
                fetched_at=datetime.now(),
            )
        quotes.append(quote)
        klines_by_code[code] = klines
        news_by_code[code] = news
        storage.save_quotes([quote])
        storage.save_klines(klines)
        storage.save_news(news)
        time.sleep(0.2)
    return quotes, klines_by_code, news_by_code


def fetch_market_structure(
    client: EastMoneyClient,
    quotes: list[StockQuote],
    allow_individual_fallback: bool = False,
) -> tuple[dict[str, CapitalFlow], dict[str, list[IntradayPoint]]]:
    flows: dict[str, CapitalFlow] = {}
    intraday: dict[str, list[IntradayPoint]] = {}
    individual_attempts = 0
    for index, quote in enumerate(quotes, start=1):
        print(f"[{index}/{len(quotes)}] 资金合力 {quote.code} {quote.name}")
        flow = client.bulk_capital_flows.get(quote.code)
        if flow is None and allow_individual_fallback:
            individual_attempts += 1
            flow = client.fetch_capital_flow(quote.code)
            if individual_attempts >= 10 and not flows and client.capital_flow_stats["error"] >= 10:
                print("资金流接口连续10次请求异常，提前终止本轮复核。")
                break
        if flow is not None:
            flows[quote.code] = flow
        # 分时接口只请求可能进入A/B组的股票，避免对全市场逐只请求并触发限流。
        if flow is not None and flow.extra_large_net is not None and flow.extra_large_net > 0 and (
            (flow.large_net or 0.0) > 0 or (flow.medium_net or 0.0) > 0
        ):
            rows = client.fetch_intraday_trends(quote.code)
            if rows:
                intraday[quote.code] = rows
            time.sleep(0.12)
    requests = individual_attempts if allow_individual_fallback else len(quotes)
    success = len(flows)
    success_rate = success / requests if requests else 0.0
    empty = max(0, requests - success)
    print(
        "资金流数据质量："
        f"成功 {success}/{requests} ({success_rate:.1%})，"
        f"空数据 {empty}。"
    )
    repeated_errors = allow_individual_fallback and client.capital_flow_stats["error"] >= 10 and success == 0
    if (requests >= 20 and success_rate < 0.5) or repeated_errors:
        raise RuntimeError(
            "资金流接口成功率低于50%，本次任务停止，避免把数据源异常误判为没有候选。"
        )
    return flows, intraday


def write_report(path: Path, picks) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "code",
                "name",
                "price",
                "pct_chg",
                "score",
                "volume_score",
                "amount_score",
                "news_score",
                "trend_score",
                "liquidity_score",
                "capital_cohesion_score",
                "distribution_penalty",
                "risk_penalty",
                "signal_group",
                "capital_flow_ratio",
                "capital_flow_persistence",
                "data_quality_score",
                "risk_flags",
                "reasons",
            ],
        )
        writer.writeheader()
        for item in picks:
            writer.writerow(item.as_row())


def print_report(picks) -> None:
    print("\n今日短线候选：")
    if not picks:
        print("未筛选出候选股票。")
        return
    for rank, item in enumerate(picks, start=1):
        price = f"{item.price:.2f}" if item.price is not None else "暂无"
        pct_chg = f"{item.pct_chg:+.2f}%" if item.pct_chg is not None else "暂无"
        print(
            f"{rank}. {item.code} {item.name} "
            f"现价 {price} 涨跌幅 {pct_chg} | 总分 {item.score:.2f} | 量 {item.volume_score:.1f} "
            f"额 {item.amount_score:.1f} 趋势 {item.trend_score:.1f} "
            f"流动性 {item.liquidity_score:.1f} 消息 {item.news_score:.1f} "
            f"资金合力 {item.capital_cohesion_score:.1f} 派发扣分 {item.distribution_penalty:.1f} "
            f"风险扣分 {item.risk_penalty:.1f}"
        )
        for reason in item.reasons:
            print(f"   - {reason}")
        for news in item.latest_news[:2]:
            print(f"   - 新闻：{news.title} ({news.source})")


def run_daemon(settings: Settings, hour: int, minute: int) -> int:
    print(f"每日任务已启动，将在 {hour:02d}:{minute:02d} 执行。按 Ctrl+C 退出。")
    last_run = ""
    while True:
        now = datetime.now()
        run_key = now.strftime("%Y-%m-%d")
        if now.hour == hour and now.minute == minute and last_run != run_key:
            run_once(settings)
            last_run = run_key
        time.sleep(30)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A 股每日短线股票分析系统")
    parser.add_argument("--once", action="store_true", help="立即运行一次抓取和推荐")
    parser.add_argument("--daemon", action="store_true", help="常驻进程，每天定时运行")
    parser.add_argument("--hour", type=int, default=17, help="定时运行小时，默认 17")
    parser.add_argument("--minute", type=int, default=30, help="定时运行分钟，默认 30")
    parser.add_argument("--max-candidates", type=int, default=None, help="参与分析的股票数量")
    parser.add_argument("--full-scan", action="store_true", help="尽量扫描全A股票池，默认最多5000只")
    parser.add_argument("--news-candidates", type=int, default=80, help="全市场初筛后抓取消息面的股票数量")
    parser.add_argument("--top-n", type=int, default=3, help="推荐股票数量")
    parser.add_argument("--news-per-stock", type=int, default=8, help="每只股票抓取新闻数量")
    parser.add_argument("--sample", action="store_true", help="使用本地样例数据验证流程，不联网")
    parser.add_argument("--debug-urls", action="store_true", help="打印真实请求的数据接口 URL")
    parser.add_argument(
        "--allow-missing-capital-flow",
        action="store_true",
        help="分单资金接口暂不可用时仍保留候选；默认严格剔除，确保资金合力优先",
    )
    parser.add_argument("--paper-trade", action="store_true", help="启用2万元本金的纸面模拟投资日志")
    parser.add_argument("--paper-capital", type=float, default=20_000.0, help="纸面模拟初始本金，默认20000")
    parser.add_argument("--paper-summary", action="store_true", help="生成纸面模拟月度复盘")
    parser.add_argument("--paper-execute-pending", action="store_true", help="仅检查并执行上一交易日待执行订单")
    parser.add_argument("--session-time", choices=("09:35", "10:30", "14:50"), default="09:35",
                        help="盘中检查点，用于价量条件和订单失效判断")
    parser.add_argument("--month", default="", help="月度复盘月份，格式 YYYY-MM；默认当前月份")
    parser.add_argument(
        "--codes",
        default="",
        help="指定股票代码，逗号分隔；可写 300308 或 300308:中际旭创。指定后不抓全市场列表",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.paper_execute_pending:
        settings = Settings(paper_capital=args.paper_capital)
        try:
            raise SystemExit(run_intraday_execution(settings, args.session_time))
        except RuntimeError as exc:
            print(f"错误：{exc}")
            raise SystemExit(1) from None
    if args.paper_summary:
        month = args.month or datetime.now().strftime("%Y-%m")
        summary_path = generate_monthly_summary(Settings().data_dir, month)
        print(f"月度复盘已保存: {summary_path}")
        raise SystemExit(0)

    requested_candidates = args.max_candidates
    if requested_candidates is None:
        requested_candidates = 5000 if args.full_scan else 120
    max_candidates = max(requested_candidates, args.top_n)
    if requested_candidates < args.top_n:
        print(f"候选池数量 {requested_candidates} 小于推荐数量 {args.top_n}，已自动提升为 {max_candidates}。")
    settings = Settings(
        max_candidates=max_candidates,
        news_candidates=max(args.news_candidates, args.top_n),
        top_n=args.top_n,
        news_per_stock=args.news_per_stock,
        use_sample_data=args.sample,
        debug_urls=args.debug_urls,
        allow_missing_capital_flow=args.allow_missing_capital_flow,
        paper_trade=args.paper_trade,
        paper_capital=args.paper_capital,
        codes=tuple(item.strip() for item in args.codes.split(",") if item.strip()),
    )
    if args.daemon:
        raise SystemExit(run_daemon(settings, args.hour, args.minute))
    try:
        raise SystemExit(run_once(settings))
    except RuntimeError as exc:
        print(f"错误：{exc}")
        raise SystemExit(1) from None
