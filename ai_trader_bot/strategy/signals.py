from __future__ import annotations

import statistics

from ..core.models import Signal


def _daily_returns(closes: list[float]) -> list[float]:
    if len(closes) < 2:
        return []
    returns: list[float] = []
    for idx in range(1, len(closes)):
        previous = closes[idx - 1]
        current = closes[idx]
        if previous <= 0:
            continue
        returns.append((current / previous) - 1.0)
    return returns


def _annualized_volatility(closes: list[float], window: int = 20) -> float:
    returns = _daily_returns(closes[-(window + 1) :])
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns) * (252**0.5)


def compute_signal(symbol: str, price: float, closes: list[float], news_score: float) -> Signal | None:
    return compute_signal_with_ai(
        symbol,
        price,
        closes,
        news_score,
        ai_short_term_score=0.0,
        ai_long_term_score=0.0,
        ai_confidence=0.0,
        ai_short_term_weight=0.0,
        ai_long_term_weight=0.0,
    )


def compute_signal_with_ai(
    symbol: str,
    price: float,
    closes: list[float],
    news_score: float,
    *,
    ai_short_term_score: float,
    ai_long_term_score: float,
    ai_confidence: float,
    ai_short_term_weight: float,
    ai_long_term_weight: float,
) -> Signal | None:
    if price <= 0 or len(closes) < 25:
        return None

    momentum_20d = (closes[-1] / closes[-21]) - 1.0
    momentum_5d = (closes[-1] / closes[-6]) - 1.0
    sma_20 = statistics.fmean(closes[-20:])
    trend_20d = (closes[-1] / sma_20) - 1.0 if sma_20 > 0 else 0.0
    volatility_20d = _annualized_volatility(closes, window=20)

    # Composite score favors trend/momentum while layering fast news + slower AI thesis.
    score = (
        (0.45 * momentum_20d)
        + (0.20 * momentum_5d)
        + (0.20 * trend_20d)
        + (0.25 * news_score)
        + (ai_short_term_weight * ai_short_term_score)
        + (ai_long_term_weight * ai_long_term_score)
        - (0.15 * min(volatility_20d, 1.0))
    )

    return Signal(
        symbol=symbol,
        price=price,
        momentum_20d=momentum_20d,
        momentum_5d=momentum_5d,
        trend_20d=trend_20d,
        volatility_20d=volatility_20d,
        news_score=news_score,
        score=score,
        ai_short_term_score=ai_short_term_score,
        ai_long_term_score=ai_long_term_score,
        ai_confidence=ai_confidence,
    )
