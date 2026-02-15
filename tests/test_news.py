from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ai_trader_bot.data.news import NewsItem, sentiment_score, source_weighted_sentiment


class NewsTests(unittest.TestCase):
    def test_sentiment_score_balances_headlines(self) -> None:
        headlines = [
            "NVIDIA reports strong growth and record demand",
            "Regulators open probe into chip export risk",
        ]
        score = sentiment_score(headlines)
        self.assertLess(score, 1.0)
        self.assertGreater(score, -1.0)

    def test_sentiment_score_positive(self) -> None:
        headlines = ["Microsoft beats expectations with strong AI growth"]
        score = sentiment_score(headlines)
        self.assertGreater(score, 0.0)

    def test_source_weighted_sentiment_applies_source_multipliers(self) -> None:
        now = datetime.now(timezone.utc)
        items = [
            NewsItem(
                title="Strong growth and record demand",
                description="",
                source="News",
                link="https://example.com/news",
                published_at=now,
                source_type="news",
            ),
            NewsItem(
                title="Probe and downgrade warning",
                description="",
                source="Analyst",
                link="https://example.com/analyst",
                published_at=now,
                source_type="analyst_rating",
            ),
        ]

        base_score, _, _ = source_weighted_sentiment(items)
        weighted_score, _, _ = source_weighted_sentiment(
            items,
            source_multipliers={"news": 2.0, "analyst_rating": 0.5},
        )
        self.assertGreater(weighted_score, base_score)


if __name__ == "__main__":
    unittest.main()
