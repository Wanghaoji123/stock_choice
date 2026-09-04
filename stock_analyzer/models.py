from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class StockQuote:
    code: str
    name: str
    market: str
    price: float | None
    pct_chg: float | None
    volume: float | None
    amount: float | None
    turnover_rate: float | None
    market_cap: float | None
    fetched_at: datetime
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    previous_close: float | None = None


@dataclass(frozen=True)
class CapitalFlow:
    """东方财富口径的当日分单净流入，单位与数据源保持一致（元）。"""

    code: str
    fetched_at: datetime
    main_net: float | None
    small_net: float | None
    medium_net: float | None
    large_net: float | None
    extra_large_net: float | None


@dataclass(frozen=True)
class IntradayPoint:
    timestamp: datetime
    price: float
    average_price: float | None
    volume: float | None
    amount: float | None


@dataclass(frozen=True)
class KLine:
    code: str
    trade_date: date
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    amplitude: float | None
    pct_chg: float | None
    turnover_rate: float | None


@dataclass(frozen=True)
class NewsItem:
    code: str
    title: str
    url: str
    source: str
    published_at: datetime | None
    summary: str


@dataclass(frozen=True)
class Recommendation:
    code: str
    name: str
    price: float | None
    pct_chg: float | None
    score: float
    volume_score: float
    amount_score: float
    news_score: float
    trend_score: float
    liquidity_score: float
    capital_cohesion_score: float
    distribution_penalty: float
    risk_penalty: float
    reasons: tuple[str, ...]
    quote: StockQuote
    latest_news: tuple[NewsItem, ...]
    signal_group: str = "C"
    capital_flow_ratio: float = 0.0
    capital_flow_persistence: int = 0
    data_quality_score: float = 0.0
    risk_flags: tuple[str, ...] = ()

    def as_row(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "price": self.price,
            "pct_chg": self.pct_chg,
            "score": round(self.score, 2),
            "volume_score": round(self.volume_score, 2),
            "amount_score": round(self.amount_score, 2),
            "news_score": round(self.news_score, 2),
            "trend_score": round(self.trend_score, 2),
            "liquidity_score": round(self.liquidity_score, 2),
            "capital_cohesion_score": round(self.capital_cohesion_score, 2),
            "distribution_penalty": round(self.distribution_penalty, 2),
            "risk_penalty": round(self.risk_penalty, 2),
            "signal_group": self.signal_group,
            "capital_flow_ratio": round(self.capital_flow_ratio, 6),
            "capital_flow_persistence": self.capital_flow_persistence,
            "data_quality_score": round(self.data_quality_score, 2),
            "risk_flags": "；".join(self.risk_flags),
            "reasons": "；".join(self.reasons),
        }
