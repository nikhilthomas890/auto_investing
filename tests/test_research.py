from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from ai_trader_bot.data.news import NewsItem
from ai_trader_bot.data.research import collect_research_items


class ResearchTests(TestCase):
    def test_collect_research_items_merges_dedupes_and_caps(self) -> None:
        now = datetime.now(timezone.utc)
        duplicate = NewsItem(
            title="NVDA beats estimates",
            description="",
            source="Google News",
            link="https://example.com/nvda",
            published_at=now,
            source_type="news",
        )
        sec_item = NewsItem(
            title="NVDA filed 10-Q with the SEC",
            description="",
            source="SEC EDGAR",
            link="https://sec.example/10q",
            published_at=now - timedelta(minutes=10),
            source_type="sec_filing",
        )
        social_item = NewsItem(
            title="Trusted account discusses NVDA demand",
            description="",
            source="Trusted Feed",
            link="https://social.example/post",
            published_at=now - timedelta(minutes=20),
            source_type="social",
        )

        with (
            patch(
                "ai_trader_bot.data.research.fetch_google_news_items",
                return_value=[duplicate, duplicate],
            ),
            patch("ai_trader_bot.data.research.fetch_sec_filings_items", return_value=[sec_item]),
            patch("ai_trader_bot.data.research.fetch_earnings_transcript_items", return_value=[]),
            patch("ai_trader_bot.data.research.fetch_social_feed_items", return_value=[social_item]),
            patch("ai_trader_bot.data.research.fetch_analyst_rating_items", return_value=[]),
        ):
            items = collect_research_items(
                "NVDA",
                "NVIDIA AI chips",
                news_lookback_hours=6,
                sec_lookback_hours=72,
                earnings_lookback_hours=336,
                social_lookback_hours=24,
                analyst_lookback_hours=720,
                max_items_per_source=5,
                total_items_cap=10,
                timeout_seconds=3.0,
                include_full_article_text=False,
                article_text_max_chars=1200,
                enable_sec_filings=True,
                sec_user_agent="ai-autotrader/0.2 (test)",
                sec_forms=["10-Q"],
                enable_earnings_transcripts=False,
                fmp_api_key="",
                earnings_transcript_max_chars=2000,
                enable_social_feeds=True,
                social_feed_rss_urls=["https://social.example/rss"],
                trusted_social_accounts=["acct1"],
                enable_analyst_ratings=False,
                finnhub_api_key="",
            )

        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].title, "NVDA beats estimates")
        self.assertEqual(items[1].source_type, "sec_filing")
        self.assertEqual(items[2].source_type, "social")

    def test_collect_research_items_enriches_full_text_for_news(self) -> None:
        now = datetime.now(timezone.utc)
        news_item = NewsItem(
            title="NVDA launches new platform",
            description="Short summary",
            source="Google News",
            link="https://example.com/story",
            published_at=now,
            source_type="news",
        )

        with (
            patch("ai_trader_bot.data.research.fetch_google_news_items", return_value=[news_item]),
            patch("ai_trader_bot.data.research.fetch_article_text", return_value="Full article body"),
        ):
            items = collect_research_items(
                "NVDA",
                "NVIDIA AI chips",
                news_lookback_hours=6,
                sec_lookback_hours=72,
                earnings_lookback_hours=336,
                social_lookback_hours=24,
                analyst_lookback_hours=720,
                max_items_per_source=5,
                total_items_cap=5,
                timeout_seconds=3.0,
                include_full_article_text=True,
                article_text_max_chars=1200,
                enable_sec_filings=False,
                sec_user_agent="ai-autotrader/0.2 (test)",
                sec_forms=["10-Q"],
                enable_earnings_transcripts=False,
                fmp_api_key="",
                earnings_transcript_max_chars=2000,
                enable_social_feeds=False,
                social_feed_rss_urls=[],
                trusted_social_accounts=[],
                enable_analyst_ratings=False,
                finnhub_api_key="",
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].content, "Full article body")


if __name__ == "__main__":
    import unittest

    unittest.main()
