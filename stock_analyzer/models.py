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
    risk_penalty: float
    reasons: tuple[str, ...]
    quote: StockQuote
    latest_news: tuple[NewsItem, ...]

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
            "risk_penalty": round(self.risk_penalty, 2),
            "reasons": "；".join(self.reasons),
        }
