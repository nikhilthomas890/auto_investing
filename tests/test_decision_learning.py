from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_trader_bot.learning.decision_learning import DecisionLearningStore, signal_feature_profile
from ai_trader_bot.core.models import Signal


class DecisionLearningTests(unittest.TestCase):
    def test_bad_call_increases_penalty_and_applies_cross_ticker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "state.json"
            journal_path = Path(tmp_dir) / "journal.jsonl"

            store = DecisionLearningStore(
                state_path=str(state_path),
                journal_path=str(journal_path),
                evaluation_horizon_hours=24,
                bad_call_return_threshold=-0.02,
                good_call_return_threshold=0.02,
                learning_rate=0.5,
                max_feature_penalty=0.75,
            )

            signal = Signal(
                symbol="NVDA",
                price=100.0,
                momentum_20d=0.10,
                momentum_5d=0.05,
                trend_20d=0.03,
                volatility_20d=0.20,
                news_score=0.40,
                score=0.18,
                ai_short_term_score=0.30,
                ai_long_term_score=0.20,
                ai_confidence=0.70,
            )
            profile = signal_feature_profile(
                signal,
                ai_short_term_weight=0.10,
                ai_long_term_weight=0.15,
            )

            store.maybe_record_call(
                signal=signal,
                feature_profile=profile,
                entry_threshold=0.012,
                option_threshold=0.035,
            )

            self.assertIn("NVDA", store.open_calls)

            # Force the call to be old enough for resolution.
            store.open_calls["NVDA"]["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
            store._save()

            resolved = store.maybe_resolve_call(symbol="NVDA", current_price=95.0)
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved["outcome"], "bad_call")

            news_penalty = store.feature_penalties["news_score"]
            self.assertGreater(news_penalty, 0.0)

            amd_like_profile = {
                "momentum_20d": 0.02,
                "momentum_5d": 0.01,
                "trend_20d": 0.01,
                "news_score": 0.10,
                "ai_short_term": 0.01,
                "ai_long_term": 0.01,
                "volatility_risk": 0.02,
            }
            adjustment = store.adjustment_for(amd_like_profile)
            self.assertLess(adjustment, 0.0)

    def test_journal_writes_open_and_resolve_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "state.json"
            journal_path = Path(tmp_dir) / "journal.jsonl"

            store = DecisionLearningStore(
                state_path=str(state_path),
                journal_path=str(journal_path),
                evaluation_horizon_hours=1,
                bad_call_return_threshold=-0.02,
                good_call_return_threshold=0.02,
                learning_rate=0.3,
                max_feature_penalty=0.75,
            )

            signal = Signal(
                symbol="AMD",
                price=50.0,
                momentum_20d=0.03,
                momentum_5d=0.02,
                trend_20d=0.01,
                volatility_20d=0.15,
                news_score=0.10,
                score=0.06,
                ai_short_term_score=0.0,
                ai_long_term_score=0.0,
                ai_confidence=0.0,
            )
            profile = signal_feature_profile(
                signal,
                ai_short_term_weight=0.10,
                ai_long_term_weight=0.15,
            )
            store.maybe_record_call(
                signal=signal,
                feature_profile=profile,
                entry_threshold=0.012,
                option_threshold=0.035,
            )

            store.open_calls["AMD"]["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
            store._save()
            store.maybe_resolve_call(symbol="AMD", current_price=52.0)

            lines = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            event_names = {line.get("event") for line in lines}
            self.assertIn("decision_call_opened", event_names)
            self.assertIn("decision_call_resolved", event_names)

    def test_source_bias_learns_from_trade_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "state.json"
            journal_path = Path(tmp_dir) / "journal.jsonl"

            store = DecisionLearningStore(
                state_path=str(state_path),
                journal_path=str(journal_path),
                evaluation_horizon_hours=1,
                bad_call_return_threshold=-0.02,
                good_call_return_threshold=0.02,
                learning_rate=0.3,
                max_feature_penalty=0.75,
                enable_source_priority_learning=True,
                source_learning_rate=0.5,
                max_source_bias=0.8,
                market_reaction_strength=0.35,
            )

            signal = Signal(
                symbol="NVDA",
                price=100.0,
                momentum_20d=0.03,
                momentum_5d=0.02,
                trend_20d=0.01,
                volatility_20d=0.15,
                news_score=0.20,
                score=0.08,
                ai_short_term_score=0.0,
                ai_long_term_score=0.0,
                ai_confidence=0.0,
            )
            profile = signal_feature_profile(
                signal,
                ai_short_term_weight=0.10,
                ai_long_term_weight=0.15,
            )
            source_profile = {
                "news": {"sentiment": 0.8, "count": 4, "multiplier": 1.0},
                "social": {"sentiment": -0.4, "count": 1, "multiplier": 1.0},
            }

            store.maybe_record_call(
                signal=signal,
                feature_profile=profile,
                source_profile=source_profile,
                entry_threshold=0.012,
                option_threshold=0.035,
            )
            store.open_calls["NVDA"]["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
            store._save()
            store.maybe_resolve_call(symbol="NVDA", current_price=110.0)

            self.assertGreater(store.source_bias.get("news", 0.0), 0.0)
            self.assertLess(store.source_bias.get("social", 0.0), 0.0)

    def test_source_bias_learns_from_market_reaction_without_trade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "state.json"
            journal_path = Path(tmp_dir) / "journal.jsonl"

            store = DecisionLearningStore(
                state_path=str(state_path),
                journal_path=str(journal_path),
                evaluation_horizon_hours=24,
                bad_call_return_threshold=-0.02,
                good_call_return_threshold=0.02,
                learning_rate=0.3,
                max_feature_penalty=0.75,
                enable_source_priority_learning=True,
                source_learning_rate=0.6,
                max_source_bias=0.8,
                market_reaction_strength=0.5,
            )

            profile = {"news": {"sentiment": 0.7, "count": 3, "multiplier": 1.0}}
            first = store.update_from_market_reaction(
                symbol="AMD",
                current_price=100.0,
                source_profile=profile,
            )
            self.assertIsNone(first)

            second = store.update_from_market_reaction(
                symbol="AMD",
                current_price=103.0,
                source_profile=profile,
            )
            self.assertIsNotNone(second)
            self.assertGreater(store.source_bias.get("news", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
