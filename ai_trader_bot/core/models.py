from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Signal:
    symbol: str
    price: float
    momentum_20d: float
    momentum_5d: float
    trend_20d: float
    volatility_20d: float
    news_score: float
    score: float
    current_news_score: float = 0.0
    historical_news_score: float = 0.0
    ai_short_term_score: float = 0.0
    ai_long_term_score: float = 0.0
    ai_confidence: float = 0.0
    macro_score: float = 0.0


@dataclass(frozen=True)
class TradeOrder:
    asset_type: Literal["EQUITY", "OPTION"]
    symbol: str
    instruction: str
    quantity: int
    limit_price: float | None
    reason: str


@dataclass
class PortfolioSnapshot:
    cash: float
    equity_positions: dict[str, int] = field(default_factory=dict)
    option_positions: dict[str, int] = field(default_factory=dict)
