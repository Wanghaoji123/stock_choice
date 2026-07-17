from __future__ import annotations

from datetime import date, datetime, timedelta

from .models import KLine, NewsItem, StockQuote


SAMPLE_STOCKS = (
    ("600519", "贵州茅台", "sh", 1518.2),
    ("300750", "宁德时代", "sz", 286.4),
    ("002415", "海康威视", "sz", 31.6),
    ("601318", "中国平安", "sh", 48.9),
    ("688981", "中芯国际", "sh", 72.5),
)


def sample_quotes() -> list[StockQuote]:
    now = datetime.now()
    rows: list[StockQuote] = []
    for index, (code, name, market, price) in enumerate(SAMPLE_STOCKS, start=1):
        rows.append(
            StockQuote(
                code=code,
                name=name,
                market=market,
                price=price,
                pct_chg=1.2 + index * 0.55,
                volume=1200000 + index * 280000,
                amount=price * (1200000 + index * 280000),
                turnover_rate=1.5 + index * 0.35,
                market_cap=price * 100000000,
                fetched_at=now,
            )
        )
    return rows


def sample_klines(code: str, price: float) -> list[KLine]:
    start = date.today() - timedelta(days=180)
    rows: list[KLine] = []
    base_volume = 820000
    for idx in range(120):
        trade_date = start + timedelta(days=idx)
        close = price * (0.82 + idx * 0.0018)
        if idx == 119:
            close = price
        volume = base_volume + idx * 2200
        if idx > 114:
            volume *= 1.65
        amount = close * volume
        rows.append(
            KLine(
                code=code,
                trade_date=trade_date,
                open=close * 0.99,
                close=close,
                high=close * 1.02,
                low=close * 0.985,
                volume=volume,
                amount=amount,
                amplitude=3.2,
                pct_chg=1.1 if idx == 119 else 0.35,
                turnover_rate=2.1,
            )
        )
    return rows


def sample_news(code: str, name: str) -> list[NewsItem]:
    now = datetime.now()
    return [
        NewsItem(
            code=code,
            title=f"{name} 获机构关注，订单与业绩增长预期升温",
            url=f"https://example.com/news/{code}/1",
            source="样例数据",
            published_at=now - timedelta(days=2),
            summary="订单、业绩预增、政策支持等关键词用于验证消息面打分。",
        ),
        NewsItem(
            code=code,
            title=f"{name} 近期成交活跃，资金关注度提升",
            url=f"https://example.com/news/{code}/2",
            source="样例数据",
            published_at=now - timedelta(days=8),
            summary="成交活跃但仍需复核公告和基本面风险。",
        ),
    ]
