from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ai_trader_bot.learning.ai_interpreter import AIOutlook
from ai_trader_bot.core.config import BotConfig
from ai_trader_bot.data.macro import MacroPolicyModel
from ai_trader_bot.data.news import NewsItem


class _FakeInterpreter:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def analyze(self, symbol: str, query: str, news_items: list[NewsItem]) -> AIOutlook:
        _ = (symbol, query, news_items)
        return AIOutlook(short_term=0.4, long_term=0.6, confidence=0.5, summary="macro")


class MacroModelTests(unittest.TestCase):
    def test_macro_model_returns_disabled_when_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = BotConfig(
                enable_macro_policy_model=False,
                macro_long_term_state_path=str(Path(tmp_dir) / "macro_state.json"),
            )
            model = MacroPolicyModel(config, _FakeInterpreter(enabled=False))
            assessment = model.evaluate()
            self.assertFalse(assessment.enabled)
            self.assertEqual(assessment.score, 0.0)

    def test_macro_model_blends_news_and_ai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = BotConfig(
                enable_macro_policy_model=True,
                macro_policy_query="policy trade deals",
                macro_news_lookback_hours=24,
                macro_news_items=10,
                macro_headline_weight=0.70,
                macro_ai_short_term_weight=0.15,
                macro_ai_long_term_weight=0.15,
                enable_full_article_text=False,
                macro_long_term_state_path=str(Path(tmp_dir) / "macro_state.json"),
                macro_long_term_memory_alpha=0.5,
            )
            model = MacroPolicyModel(config, _FakeInterpreter(enabled=True))

            items = [
                NewsItem(
                    title="Government trade deal breakthrough supports growth",
                    description="",
                    source="Test",
                    link="",
                    published_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                    source_type="news",
                )
            ]
            with patch("ai_trader_bot.data.macro.fetch_google_news_items", return_value=items):
                assessment = model.evaluate(lookback_hours_override=12)

            self.assertTrue(assessment.enabled)
            self.assertEqual(assessment.item_count, 1)
            self.assertEqual(assessment.lookback_hours, 12)
            self.assertGreater(assessment.score, 0.0)
            self.assertGreater(assessment.ai_long_term, 0.0)


if __name__ == "__main__":
    unittest.main()

