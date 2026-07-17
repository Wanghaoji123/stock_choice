from __future__ import annotations


POSITIVE_KEYWORDS = {
    "业绩预增": 4.0,
    "净利润增长": 3.5,
    "订单": 2.5,
    "中标": 3.0,
    "回购": 2.0,
    "增持": 2.5,
    "突破": 2.0,
    "涨停": 2.0,
    "创新高": 2.5,
    "政策支持": 2.5,
    "国产替代": 1.8,
    "人工智能": 1.5,
    "算力": 1.5,
    "半导体": 1.5,
    "新能源": 1.2,
    "重组": 2.0,
}

NEGATIVE_KEYWORDS = {
    "业绩预亏": -4.0,
    "亏损": -3.0,
    "减持": -3.0,
    "立案": -4.0,
    "调查": -2.5,
    "问询函": -2.0,
    "处罚": -3.5,
    "退市": -5.0,
    "跌停": -3.0,
    "解禁": -1.8,
    "商誉减值": -3.0,
    "债务": -2.5,
}


def score_text(text: str) -> float:
    normalized = text.upper()
    score = 0.0
    for keyword, value in POSITIVE_KEYWORDS.items():
        if keyword.upper() in normalized:
            score += value
    for keyword, value in NEGATIVE_KEYWORDS.items():
        if keyword.upper() in normalized:
            score += value
    return max(-10.0, min(10.0, score))
