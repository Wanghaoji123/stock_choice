from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

from .analyzer import build_recommendations
from .config import Settings
from .fetchers import EastMoneyClient
from .models import KLine, NewsItem, StockQuote
from .sample_data import sample_klines, sample_news, sample_quotes
from .storage import Storage


def run_once(settings: Settings) -> int:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    client = EastMoneyClient(settings)
    storage = Storage(settings.db_path)
    try:
        if settings.codes:
            quotes, klines_by_code, news_by_code = fetch_explicit_codes(client, storage, settings)
            picks = build_recommendations(quotes, klines_by_code, news_by_code, settings.top_n)
            run_date = datetime.now().strftime("%Y-%m-%d")
            storage.save_recommendations(run_date, picks)
            write_report(settings.data_dir / f"recommendations_{run_date}.csv", picks)
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
            time.sleep(0.2)

        if not settings.use_sample_data:
            shortlist_size = min(len(quotes), max(settings.top_n, settings.news_candidates))
            print(f"第一阶段完成：已对 {len(klines_by_code)} 只股票做资金/趋势/流动性初筛。")
            print(f"第二阶段：对初筛前 {shortlist_size} 只抓取消息面，再输出最终 {settings.top_n} 只。")
            preliminary = build_recommendations(quotes, klines_by_code, {}, shortlist_size)
            quote_by_code = {quote.code: quote for quote in quotes}
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

        picks = build_recommendations(final_quotes, klines_by_code, news_by_code, settings.top_n)
        run_date = datetime.now().strftime("%Y-%m-%d")
        storage.save_recommendations(run_date, picks)
        write_report(settings.data_dir / f"recommendations_{run_date}.csv", picks)
        print_report(picks)
        print(f"\n报告已保存: {settings.data_dir / f'recommendations_{run_date}.csv'}")
        print("提示：这是量化和新闻情绪筛选结果，不构成投资建议；买卖前请人工复核公告、财报和风险。")
        return 0
    finally:
        storage.close()


def parse_code_item(item: str) -> tuple[str, str]:
    if ":" in item:
        code, name = item.split(":", 1)
        return code.strip(), name.strip()
    return item.strip(), item.strip()


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
                "risk_penalty",
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
        "--codes",
        default="",
        help="指定股票代码，逗号分隔；可写 300308 或 300308:中际旭创。指定后不抓全市场列表",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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
        codes=tuple(item.strip() for item in args.codes.split(",") if item.strip()),
    )
    if args.daemon:
        raise SystemExit(run_daemon(settings, args.hour, args.minute))
    raise SystemExit(run_once(settings))
