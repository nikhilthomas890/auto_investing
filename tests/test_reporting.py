from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ai_trader_bot.core.config import BotConfig
from ai_trader_bot.reporting import ReportManager


class ReportingTests(unittest.TestCase):
    def _config(self, tmp_dir: str) -> BotConfig:
        base = Path(tmp_dir)
        return BotConfig(
            report_subject_prefix="AI Trader",
            activity_log_path=str(base / "activity.jsonl"),
            portfolio_log_path=str(base / "portfolio.jsonl"),
            metadata_log_path=str(base / "metadata.jsonl"),
            report_state_path=str(base / "report_state.json"),
            daily_report_log_path=str(base / "daily_report.jsonl"),
            weekly_report_log_path=str(base / "weekly_report.jsonl"),
            research_log_path=str(base / "research_log.jsonl"),
            quarterly_model_advisor_log_path=str(base / "quarterly_model_advisor.jsonl"),
            model_roadmap_log_path=str(base / "model_roadmap_advisor.jsonl"),
            bootstrap_optimization_log_path=str(base / "bootstrap_optimization_report.jsonl"),
            layer_reevaluation_log_path=str(base / "layer_reevaluation_report.jsonl"),
            decision_journal_path=str(base / "decision_journal.jsonl"),
            decision_learning_state_path=str(base / "decision_state.json"),
        )

    def test_daily_digest_contains_per_stock_reasoning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._config(tmp_dir)
            manager = ReportManager(config)

            timestamp = datetime(2026, 2, 14, 18, 0, tzinfo=timezone.utc)
            summary = {
                "cash": 450.0,
                "account_equity": 1005.0,
                "equity_positions": {"NVDA": 1},
                "option_positions": {},
                "orders": [
                    {
                        "asset_type": "EQUITY",
                        "symbol": "NVDA",
                        "instruction": "BUY",
                        "quantity": 1,
                        "limit_price": 500.0,
                        "reason": "signal_entry_0.0800",
                    }
                ],
                "signal_map": {
                    "NVDA": {
                        "symbol": "NVDA",
                        "score": 0.08,
                        "momentum_20d": 0.12,
                        "momentum_5d": 0.03,
                        "trend_20d": 0.04,
                        "news_score": 0.2,
                        "ai_short_term_score": 0.1,
                        "ai_long_term_score": 0.15,
                        "volatility_20d": 0.35,
                    }
                },
            }

            manager.record_cycle(summary, timestamp=timestamp)
            digest = manager.build_daily_digest(timestamp.date())
            self.assertIsNotNone(digest)
            assert digest is not None
            subject, body = digest

            self.assertIn("Daily Decision Digest", subject)
            self.assertIn("NVDA", body)
            self.assertIn("Buy decision", body)
            self.assertIn("research sentiment", body)
            self.assertIn("System metadata", body)
            self.assertIn("Quarter goal", body)

    def test_weekly_digest_computes_portfolio_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._config(tmp_dir)
            manager = ReportManager(config)

            start = datetime(2026, 2, 8, 12, 0, tzinfo=timezone.utc)
            for day_idx in range(7):
                ts = start + timedelta(days=day_idx)
                summary = {
                    "cash": 500.0 + day_idx,
                    "account_equity": 1000.0 + (day_idx * 10),
                    "equity_positions": {},
                    "option_positions": {},
                    "orders": [],
                    "signal_map": {},
                }
                manager.record_cycle(summary, timestamp=ts)

            digest = manager.build_weekly_digest(end_date=(start + timedelta(days=6)).date())
            self.assertIsNotNone(digest)
            assert digest is not None
            subject, body = digest

            self.assertIn("Weekly Portfolio Summary", subject)
            self.assertIn("Start equity", body)
            self.assertIn("End equity", body)
            self.assertIn("Weekly change", body)

    def test_quarterly_model_advisor_digest_recommends_strengths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._config(tmp_dir)
            manager = ReportManager(config)

            # Q1 sample data.
            for ts, equity in (
                (datetime(2026, 1, 10, 18, 0, tzinfo=timezone.utc), 1000.0),
                (datetime(2026, 2, 10, 18, 0, tzinfo=timezone.utc), 960.0),
                (datetime(2026, 3, 25, 18, 0, tzinfo=timezone.utc), 1045.0),
            ):
                summary = {
                    "cash": equity * 0.4,
                    "account_equity": equity,
                    "equity_positions": {"NVDA": 1},
                    "option_positions": {},
                    "orders": [],
                    "signal_map": {},
                    "decision_metadata": {"orders_proposed": 0},
                    "collection_metadata": {},
                    "execute_orders": True,
                }
                manager.record_cycle(summary, timestamp=ts)

            journal_path = Path(config.decision_journal_path)
            with journal_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    '{"event":"decision_call_resolved","timestamp":"2026-03-20T18:00:00+00:00","outcome":"good_call"}\n'
                )
                handle.write(
                    '{"event":"decision_call_resolved","timestamp":"2026-03-22T18:00:00+00:00","outcome":"bad_call"}\n'
                )

            digest = manager.build_quarterly_model_advisor_digest(date(2026, 4, 1))
            self.assertIsNotNone(digest)
            assert digest is not None
            subject, body = digest

            self.assertIn("Quarterly Model Advisor", subject)
            self.assertIn("Recommended model-strength changes", body)
            self.assertIn("Historical Research Weight", body)

    def test_model_roadmap_advisor_digest_for_q1_contains_new_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._config(tmp_dir)
            manager = ReportManager(config)

            # Q4 sample data for Q1 advisory.
            for ts, equity in (
                (datetime(2025, 10, 15, 18, 0, tzinfo=timezone.utc), 1000.0),
                (datetime(2025, 11, 20, 18, 0, tzinfo=timezone.utc), 940.0),
                (datetime(2025, 12, 20, 18, 0, tzinfo=timezone.utc), 980.0),
            ):
                summary = {
                    "cash": equity * 0.4,
                    "account_equity": equity,
                    "equity_positions": {"NVDA": 1},
                    "option_positions": {},
                    "orders": [],
                    "signal_map": {},
                    "decision_metadata": {"orders_proposed": 0},
                    "collection_metadata": {"research_items_by_source": {"news": 4}},
                    "execute_orders": True,
                }
                manager.record_cycle(summary, timestamp=ts)

            digest = manager.build_model_roadmap_advisor_digest(date(2026, 1, 1))
            self.assertIsNotNone(digest)
            assert digest is not None
            subject, body = digest
            self.assertIn("Model Roadmap Advisor", subject)
            self.assertIn("Recommended new models to build", body)
            self.assertIn("estimated implementation effort", body)

    def test_scheduler_emits_both_advisors_when_same_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._config(tmp_dir)
            config.quarterly_model_advisor_reminder_days = 14
            config.model_roadmap_reminder_days = 14
            config.model_roadmap_target_quarters = [1, 3]
            manager = ReportManager(config)

            summary = {
                "cash": 500.0,
                "account_equity": 1000.0,
                "equity_positions": {},
                "option_positions": {},
                "orders": [],
                "signal_map": {},
                "decision_metadata": {"orders_proposed": 0},
                "collection_metadata": {},
                "execute_orders": True,
            }
            manager.record_cycle(summary, timestamp=datetime(2025, 12, 10, 18, 0, tzinfo=timezone.utc))

            # 14 days before 2026-01-01 at 6 PM ET.
            manager.maybe_send_scheduled_reports(now=datetime(2025, 12, 18, 23, 0, tzinfo=timezone.utc))

            self.assertEqual(manager.state.last_quarterly_advisor_target, "2026-01-01")
            self.assertEqual(manager.state.last_model_roadmap_target, "2026-01-01")

            quarterly_lines = [
                json.loads(line)
                for line in Path(config.quarterly_model_advisor_log_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            roadmap_lines = [
                json.loads(line)
                for line in Path(config.model_roadmap_log_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(row.get("event") == "quarterly_model_advisor" for row in quarterly_lines))
            self.assertTrue(any(row.get("event") == "model_roadmap_advisor" for row in roadmap_lines))
            quarterly_event = next(row for row in quarterly_lines if row.get("event") == "quarterly_model_advisor")
            roadmap_event = next(row for row in roadmap_lines if row.get("event") == "model_roadmap_advisor")
            self.assertIsInstance(quarterly_event.get("metrics"), dict)
            self.assertIsInstance(quarterly_event.get("comparison"), dict)
            self.assertIsInstance(quarterly_event.get("recommendations"), list)
            self.assertIsInstance(roadmap_event.get("metrics"), dict)
            self.assertIsInstance(roadmap_event.get("comparison"), dict)
            self.assertIsInstance(roadmap_event.get("recommendations"), list)
            first_recommendation = roadmap_event.get("recommendations")[0]
            self.assertIn("effort", first_recommendation)
            self.assertIn("estimate", first_recommendation)

    def test_bootstrap_daily_optimization_report_logs_metrics_and_suggestions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._config(tmp_dir)
            manager = ReportManager(config)

            ts_1 = datetime(2026, 2, 18, 15, 0, tzinfo=timezone.utc)
            ts_2 = datetime(2026, 2, 18, 17, 0, tzinfo=timezone.utc)
            summary_base = {
                "cash": 1000.0,
                "account_equity": 1000.0,
                "equity_positions": {},
                "option_positions": {},
                "orders": [],
                "signal_map": {},
                "execute_orders": False,
                "decision_metadata": {
                    "signals_generated": 2,
                    "orders_proposed": 0,
                },
                "collection_metadata": {
                    "symbols_analyzed": 3,
                    "symbols_with_market_data": 3,
                    "symbols_with_research": 2,
                    "research_items_total": 8,
                    "research_items_by_source": {"news": 4, "sec": 2, "analyst": 2},
                    "historical_pattern_feedback_events": 1,
                },
                "bootstrap": {"active": True},
            }
            manager.record_cycle(summary_base, timestamp=ts_1)
            manager.record_cycle(summary_base, timestamp=ts_2)

            # 6 PM ET trigger.
            manager.maybe_send_scheduled_reports(now=datetime(2026, 2, 18, 23, 5, tzinfo=timezone.utc))

            self.assertEqual(manager.state.last_bootstrap_optimization_date, "2026-02-18")
            log_rows = [
                json.loads(line)
                for line in Path(config.bootstrap_optimization_log_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            event = next(row for row in log_rows if row.get("event") == "bootstrap_optimization_report")
            self.assertIsInstance(event.get("metrics"), dict)
            self.assertIsInstance(event.get("comparison"), dict)
            self.assertIsInstance(event.get("suggestions"), list)

    def test_daily_report_and_research_items_are_logged_for_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._config(tmp_dir)
            manager = ReportManager(config)

            ts = datetime(2026, 2, 18, 20, 0, tzinfo=timezone.utc)
            summary = {
                "cash": 900.0,
                "account_equity": 1000.0,
                "equity_positions": {"NVDA": 1},
                "option_positions": {},
                "orders": [],
                "signal_map": {},
                "execute_orders": False,
                "decision_metadata": {"signals_generated": 1, "orders_proposed": 0},
                "collection_metadata": {"symbols_with_market_data": 1, "symbols_with_research": 1},
                "research_items": [
                    {
                        "symbol": "NVDA",
                        "source_type": "news",
                        "source": "Example",
                        "title": "NVDA update",
                        "description": "Sample description",
                        "summary": "Sample summary",
                        "key_points": ["Point A", "Point B"],
                        "link": "https://example.com/nvda",
                        "published_at": "2026-02-18T19:00:00+00:00",
                    }
                ],
            }
            manager.record_cycle(summary, timestamp=ts)
            manager.record_cycle(summary, timestamp=ts)  # duplicate should dedupe research item

            manager.maybe_send_scheduled_reports(now=datetime(2026, 2, 18, 23, 5, tzinfo=timezone.utc))

            daily_rows = [
                json.loads(line)
                for line in Path(config.daily_report_log_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(row.get("event") == "daily_report" for row in daily_rows))

            research_rows = [
                json.loads(line)
                for line in Path(config.research_log_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(research_rows), 1)
            self.assertEqual(research_rows[0].get("event"), "research_item")

    def test_layer_reevaluation_report_is_generated_with_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = self._config(tmp_dir)
            config.weekly_report_day_local = "FRI"
            config.weekly_report_hour_local = 18
            manager = ReportManager(config)

            for ts, equity in (
                (datetime(2026, 2, 14, 18, 0, tzinfo=timezone.utc), 1000.0),
                (datetime(2026, 2, 15, 18, 0, tzinfo=timezone.utc), 995.0),
                (datetime(2026, 2, 16, 18, 0, tzinfo=timezone.utc), 1008.0),
            ):
                summary = {
                    "cash": equity * 0.5,
                    "account_equity": equity,
                    "equity_positions": {"NVDA": 1},
                    "option_positions": {},
                    "orders": [],
                    "signal_map": {},
                    "execute_orders": True,
                    "decision_metadata": {
                        "signals_generated": 2,
                        "orders_proposed": 0,
                        "llm_first_enabled": True,
                        "llm_plan_generated": True,
                        "llm_plan_used": False,
                        "llm_plan_confidence": 0.30,
                    },
                    "collection_metadata": {"research_items_by_source": {"news": 4}},
                }
                manager.record_cycle(summary, timestamp=ts)

            journal_path = Path(config.decision_journal_path)
            with journal_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    '{"event":"decision_call_resolved","timestamp":"2026-02-15T18:00:00+00:00","outcome":"good_call"}\n'
                )
                handle.write(
                    '{"event":"decision_call_resolved","timestamp":"2026-02-16T18:00:00+00:00","outcome":"bad_call","why_bad":["ai_thesis_miss"]}\n'
                )

            # Friday Feb 20, 2026 18:05 ET.
            manager.maybe_send_scheduled_reports(now=datetime(2026, 2, 20, 23, 5, tzinfo=timezone.utc))

            rows = [
                json.loads(line)
                for line in Path(config.layer_reevaluation_log_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            event = next(row for row in rows if row.get("event") == "layer_reevaluation_report")
            self.assertIn("Layer Reevaluation Report", str(event.get("subject") or ""))
            self.assertIsInstance(event.get("metrics"), dict)
            self.assertIsInstance(event.get("comparison"), dict)
            recommendations = event.get("recommendations")
            self.assertIsInstance(recommendations, list)
            if isinstance(recommendations, list):
                self.assertTrue(any(isinstance(item, dict) and item.get("layer") == "L1" for item in recommendations))


if __name__ == "__main__":
    unittest.main()
