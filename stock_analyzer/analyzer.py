from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from .models import KLine, NewsItem, Recommendation, StockQuote
from .sentiment import score_text


def _safe_mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _ratio_score(current: float | None, baseline: float, cap: float = 100.0) -> float:
    if current is None or baseline <= 0:
        return 0.0
    ratio = current / baseline
    if ratio >= 2.5:
        return cap
    if ratio >= 1.8:
        return 82.0
    if ratio >= 1.3:
        return 65.0
    if ratio >= 1.0:
        return 48.0
    return max(0.0, ratio * 40.0)


def _momentum_bonus(klines: list[KLine]) -> float:
    if len(klines) < 20:
        return 0.0
    last = klines[-1].close
    ma5 = _safe_mean([row.close for row in klines[-5:]])
    ma20 = _safe_mean([row.close for row in klines[-20:]])
    bonus = 0.0
    if last > ma5 > ma20:
        bonus += 12.0
    if klines[-1].pct_chg and 0 < klines[-1].pct_chg < 8.5:
        bonus += min(8.0, klines[-1].pct_chg)
    if klines[-1].pct_chg and klines[-1].pct_chg <= -4:
        bonus -= 10.0
    return bonus


def _trend_score(klines: list[KLine]) -> tuple[float, list[str]]:
    if len(klines) < 60:
        return 35.0, ["K线不足60日，趋势分保守处理"]
    closes = [row.close for row in klines]
    last = closes[-1]
    ma5 = _safe_mean(closes[-5:])
    ma10 = _safe_mean(closes[-10:])
    ma20 = _safe_mean(closes[-20:])
    ma60 = _safe_mean(closes[-60:])
    high_60 = max(closes[-60:])
    low_60 = min(closes[-60:])
    ret_5 = (last - closes[-6]) / closes[-6] * 100 if closes[-6] else 0.0
    ret_20 = (last - closes[-21]) / closes[-21] * 100 if closes[-21] else 0.0

    score = 45.0
    reasons: list[str] = []
    if last > ma5 > ma10 > ma20:
        score += 22.0
        reasons.append("短中期均线多头排列")
    elif last > ma20:
        score += 10.0
        reasons.append("价格站上20日均线")
    else:
        score -= 15.0
        reasons.append("价格弱于20日均线")
    if ma20 > ma60:
        score += 12.0
        reasons.append("20日均线强于60日均线")
    if 0 < ret_5 < 18:
        score += 8.0
        reasons.append(f"近5日涨幅 {ret_5:.1f}%，动量温和")
    elif ret_5 >= 18:
        score -= 10.0
        reasons.append(f"近5日涨幅 {ret_5:.1f}%，短线偏拥挤")
    if 0 < ret_20 < 35:
        score += 8.0
    elif ret_20 <= -12:
        score -= 12.0
    if high_60 > 0 and last >= high_60 * 0.92:
        score += 8.0
        reasons.append("价格接近60日高位，趋势关注度较高")
    if low_60 > 0 and last <= low_60 * 1.08:
        score -= 8.0
    return max(0.0, min(100.0, score)), reasons[:3]


def _liquidity_score(quote: StockQuote, latest: KLine) -> tuple[float, list[str]]:
    amount = quote.amount or latest.amount
    turnover = quote.turnover_rate or latest.turnover_rate
    score = 35.0
    reasons: list[str] = []
    if amount >= 10_000_000_000:
        score += 40.0
        reasons.append("成交额超过100亿，流动性充足")
    elif amount >= 3_000_000_000:
        score += 28.0
        reasons.append("成交额超过30亿，短线容量较好")
    elif amount >= 1_000_000_000:
        score += 16.0
        reasons.append("成交额超过10亿")
    else:
        score -= 12.0
        reasons.append("成交额偏低，短线容量一般")
    if turnover is not None:
        if 2 <= turnover <= 12:
            score += 18.0
            reasons.append(f"换手率 {turnover:.1f}%，活跃度合适")
        elif turnover > 20:
            score -= 12.0
            reasons.append(f"换手率 {turnover:.1f}%，筹码波动偏大")
        elif turnover < 0.8:
            score -= 10.0
            reasons.append(f"换手率 {turnover:.1f}%，活跃度不足")
    return max(0.0, min(100.0, score)), reasons[:2]


def _risk_penalty(quote: StockQuote, klines: list[KLine], news: list[NewsItem]) -> tuple[float, list[str]]:
    penalty = 0.0
    reasons: list[str] = []
    latest = klines[-1]
    pct_chg = quote.pct_chg if quote.pct_chg is not None else latest.pct_chg
    if pct_chg is not None:
        if pct_chg >= 9.5:
            penalty += 18.0
            reasons.append("当日接近涨停，次周追高风险增加")
        if pct_chg <= -7:
            penalty += 14.0
            reasons.append("当日大跌，需防趋势破位")
    if len(klines) >= 20:
        closes = [row.close for row in klines[-20:]]
        drawdown = (max(closes) - closes[-1]) / max(closes) * 100 if max(closes) else 0.0
        if drawdown >= 18:
            penalty += 12.0
            reasons.append(f"20日高点回撤 {drawdown:.1f}%，风险偏高")
    text = " ".join(f"{item.title} {item.summary}" for item in news[:8])
    severe_tokens = ("立案", "退市", "处罚", "业绩预亏", "减持", "问询函")
    if any(token in text for token in severe_tokens):
        penalty += 18.0
        reasons.append("消息面存在监管、减持或业绩风险关键词")
    return penalty, reasons[:2]


def score_news(news: list[NewsItem]) -> tuple[float, list[str]]:
    if not news:
        return 0.0, ["未抓取到近期相关新闻，消息面不给加分"]
    cutoff = datetime.now() - timedelta(days=365)
    recent = [item for item in news if item.published_at is None or item.published_at >= cutoff]
    scored = []
    for item in recent:
        scored.append((score_text(f"{item.title} {item.summary}"), item))
    total = sum(value for value, _ in scored)
    score = max(0.0, min(100.0, 45.0 + total * 5.0 + min(len(scored), 8) * 2.0))
    reasons = []
    positives = [item.title for value, item in scored if value > 0]
    negatives = [item.title for value, item in scored if value < 0]
    if positives:
        reasons.append(f"消息面偏积极：{positives[0][:32]}")
    if negatives:
        reasons.append(f"存在负面消息需复核：{negatives[0][:32]}")
    if not reasons:
        reasons.append(f"近一年抓取到 {len(recent)} 条相关新闻，情绪中性")
    return score, reasons


def build_recommendations(
    quotes: list[StockQuote],
    klines_by_code: dict[str, list[KLine]],
    news_by_code: dict[str, list[NewsItem]],
    top_n: int,
) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    for quote in quotes:
        klines = klines_by_code.get(quote.code) or []
        if len(klines) < 30:
            continue
        last_60 = klines[-60:]
        volume_baseline = _safe_mean([row.volume for row in last_60[:-1]])
        amount_baseline = _safe_mean([row.amount for row in last_60[:-1]])
        latest = last_60[-1]
        volume_score = _ratio_score(quote.volume or latest.volume, volume_baseline)
        amount_score = _ratio_score(quote.amount or latest.amount, amount_baseline)
        news = news_by_code.get(quote.code) or []
        news_score, news_reasons = score_news(news)
        trend_score, trend_reasons = _trend_score(klines)
        liquidity_score, liquidity_reasons = _liquidity_score(quote, latest)
        risk_penalty, risk_reasons = _risk_penalty(quote, klines, news)
        capital_score = volume_score * 0.5 + amount_score * 0.5
        score = (
            capital_score * 0.35
            + trend_score * 0.25
            + news_score * 0.20
            + liquidity_score * 0.15
            - risk_penalty
        )

        reasons = [
            f"成交量相对60日均量 {((quote.volume or latest.volume) / volume_baseline):.2f} 倍"
            if volume_baseline > 0
            else "成交量基准不足",
            f"成交额相对60日均额 {((quote.amount or latest.amount) / amount_baseline):.2f} 倍"
            if amount_baseline > 0
            else "成交额基准不足",
            *trend_reasons,
            *liquidity_reasons,
            *news_reasons,
            *risk_reasons,
        ]
        recommendations.append(
            Recommendation(
                code=quote.code,
                name=quote.name,
                price=quote.price,
                pct_chg=quote.pct_chg,
                score=max(0.0, score),
                volume_score=volume_score,
                amount_score=amount_score,
                news_score=news_score,
                trend_score=trend_score,
                liquidity_score=liquidity_score,
                risk_penalty=risk_penalty,
                reasons=tuple(reasons[:6]),
                quote=quote,
                latest_news=tuple((news_by_code.get(quote.code) or [])[:3]),
            )
        )
    recommendations.sort(key=lambda item: item.score, reverse=True)
    return recommendations[:top_n]
