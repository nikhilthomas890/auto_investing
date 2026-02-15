from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.config import BotConfig
from ..learning.ai_interpreter import LongTermMemoryStore, OpenAINewsInterpreter
from .news import fetch_google_news_items, source_weighted_sentiment
from .research import enrich_with_full_text


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class MacroAssessment:
    enabled: bool
    score: float
    headline_sentiment: float
    ai_short_term: float
    ai_long_term: float
    ai_confidence: float
    item_count: int
    lookback_hours: int
    query: str


class MacroPolicyModel:
    def __init__(self, config: BotConfig, ai_interpreter: OpenAINewsInterpreter) -> None:
        self.config = config
        self.ai_interpreter = ai_interpreter
        self.enabled = bool(config.enable_macro_policy_model)
        self.query = config.macro_policy_query.strip()
        self.long_term_memory = LongTermMemoryStore(
            config.macro_long_term_state_path,
            config.macro_long_term_memory_alpha,
        )

    def evaluate(self, *, lookback_hours_override: int | None = None) -> MacroAssessment:
        lookback_hours = (
            max(1, int(lookback_hours_override))
            if lookback_hours_override is not None
            else max(1, int(self.config.macro_news_lookback_hours))
        )

        if not self.enabled or not self.query:
            return MacroAssessment(
                enabled=False,
                score=0.0,
                headline_sentiment=0.0,
                ai_short_term=0.0,
                ai_long_term=self.long_term_memory.get("MACRO"),
                ai_confidence=0.0,
                item_count=0,
                lookback_hours=lookback_hours,
                query=self.query,
            )

        try:
            items = fetch_google_news_items(
                self.query,
                lookback_hours=lookback_hours,
                max_items=max(1, self.config.macro_news_items),
                timeout_seconds=self.config.request_timeout_seconds,
            )
        except Exception as exc:
            logging.warning("Macro policy news lookup failed: %s", exc)
            items = []

        if self.config.enable_full_article_text and items:
            items = enrich_with_full_text(
                items,
                timeout_seconds=self.config.request_timeout_seconds,
                max_chars=max(200, self.config.article_text_max_chars),
            )

        headline_sentiment, _, _ = source_weighted_sentiment(items)

        ai_short_term = 0.0
        ai_confidence = 0.0
        ai_long_term = self.long_term_memory.get("MACRO")
        if self.ai_interpreter.enabled and items:
            outlook = self.ai_interpreter.analyze("MACRO", self.query, items)
            ai_confidence = _clamp(outlook.confidence, 0.0, 1.0)
            ai_short_term = _clamp(outlook.short_term, -1.0, 1.0) * ai_confidence
            fresh_long_term = _clamp(outlook.long_term, -1.0, 1.0) * ai_confidence
            ai_long_term = self.long_term_memory.update("MACRO", fresh_long_term)

        score = _clamp(
            (self.config.macro_headline_weight * headline_sentiment)
            + (self.config.macro_ai_short_term_weight * ai_short_term)
            + (self.config.macro_ai_long_term_weight * ai_long_term),
            -1.0,
            1.0,
        )

        return MacroAssessment(
            enabled=True,
            score=score,
            headline_sentiment=headline_sentiment,
            ai_short_term=ai_short_term,
            ai_long_term=ai_long_term,
            ai_confidence=ai_confidence,
            item_count=len(items),
            lookback_hours=lookback_hours,
            query=self.query,
        )
