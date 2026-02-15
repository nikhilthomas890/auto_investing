from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ai_trader_bot.core.config import BotConfig


class ConfigTests(unittest.TestCase):
    def test_from_env_parses_int_and_csv_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "REBALANCE_INTERVAL_SECONDS": "180",
                "SOCIAL_FEED_RSS_URLS": "https://example.com/feed1.xml, https://example.com/feed2.xml",
                "TRUSTED_SOCIAL_ACCOUNTS": "@acct1,acct2",
                "MARKET_PREMARKET_START_HOUR_LOCAL": "7",
                "RUNTIME_SHUTDOWN_HOUR_LOCAL": "18",
                "STARTUP_CATCHUP_DEFAULT_HOURS": "96",
                "DECISION_RESEARCH_LOOKBACK_HOURS": "168",
                "HISTORICAL_RESEARCH_WEIGHT": "0.25",
                "MODEL_ROADMAP_TARGET_QUARTERS": "Q1,Q3",
                "QUARTERLY_GOAL_TARGET_EQUITY": "1500",
                "METADATA_LOG_PATH": "metadata_log.jsonl",
                "RESTRICT_FUND_TRANSFERS": "true",
                "CONTROL_ACTIONS_LOG_PATH": "control_actions.jsonl",
                "CONTROL_RESULTS_LOG_PATH": "control_results.jsonl",
                "RUNTIME_OVERRIDES_PATH": "runtime_overrides.json",
                "MODEL_BUILD_REQUESTS_PATH": "model_requests.jsonl",
                "CONTROL_MAX_ACTIONS_PER_CYCLE": "15",
                "CONTROL_AUTO_APPLY_ON_SUBMIT": "false",
                "CONTROL_AUTO_RESTART_ON_REQUEST": "true",
                "CONTROL_REDEPLOY_COMMAND": "echo deploy",
                "CONTROL_REDEPLOY_TIMEOUT_SECONDS": "120",
                "ENABLE_LLM_FIRST_DECISIONING": "true",
                "LLM_FIRST_MAX_SYMBOLS": "10",
                "LLM_FIRST_MIN_CONFIDENCE": "0.55",
                "LLM_FIRST_REQUIRE_SIGNALS_FOR_ENTRIES": "false",
                "LLM_SUPPORT_MIN_SIGNAL_SCORE": "-0.01",
                "ENABLE_LAYER_REEVALUATION_REPORTS": "true",
                "LAYER_REEVALUATION_LOG_PATH": "layer_reevaluation_report.jsonl",
            },
            clear=False,
        ):
            config = BotConfig.from_env()

        self.assertEqual(config.rebalance_interval_seconds, 180)
        self.assertEqual(
            config.social_feed_rss_urls,
            ["https://example.com/feed1.xml", "https://example.com/feed2.xml"],
        )
        self.assertEqual(config.trusted_social_accounts, ["@acct1", "acct2"])
        self.assertEqual(config.market_premarket_start_hour_local, 7)
        self.assertEqual(config.runtime_shutdown_hour_local, 18)
        self.assertEqual(config.startup_catchup_default_hours, 96)
        self.assertEqual(config.decision_research_lookback_hours, 168)
        self.assertAlmostEqual(config.historical_research_weight, 0.25, places=6)
        self.assertEqual(config.model_roadmap_target_quarters, [1, 3])
        self.assertEqual(config.quarterly_goal_target_equity, 1500.0)
        self.assertEqual(config.metadata_log_path, "metadata_log.jsonl")
        self.assertTrue(config.restrict_fund_transfers)
        self.assertEqual(config.control_actions_log_path, "control_actions.jsonl")
        self.assertEqual(config.control_results_log_path, "control_results.jsonl")
        self.assertEqual(config.runtime_overrides_path, "runtime_overrides.json")
        self.assertEqual(config.model_build_requests_path, "model_requests.jsonl")
        self.assertEqual(config.control_max_actions_per_cycle, 15)
        self.assertFalse(config.control_auto_apply_on_submit)
        self.assertTrue(config.control_auto_restart_on_request)
        self.assertEqual(config.control_redeploy_command, "echo deploy")
        self.assertEqual(config.control_redeploy_timeout_seconds, 120)
        self.assertTrue(config.enable_llm_first_decisioning)
        self.assertEqual(config.llm_first_max_symbols, 10)
        self.assertAlmostEqual(config.llm_first_min_confidence, 0.55, places=6)
        self.assertFalse(config.llm_first_require_signals_for_entries)
        self.assertAlmostEqual(config.llm_support_min_signal_score, -0.01, places=6)
        self.assertTrue(config.enable_layer_reevaluation_reports)
        self.assertEqual(config.layer_reevaluation_log_path, "layer_reevaluation_report.jsonl")

    def test_live_trading_remains_disabled_without_greenlight(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LIVE_TRADING": "true",
                "LIVE_TRADING_GREENLIGHT": "false",
            },
            clear=False,
        ):
            config = BotConfig.from_env(force_live=True)

        self.assertTrue(config.live_trading_requested)
        self.assertFalse(config.live_trading_greenlight)
        self.assertFalse(config.live_trading)

    def test_live_trading_enables_only_with_greenlight(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LIVE_TRADING": "true",
                "LIVE_TRADING_GREENLIGHT": "true",
            },
            clear=False,
        ):
            config = BotConfig.from_env()

        self.assertTrue(config.live_trading_requested)
        self.assertTrue(config.live_trading_greenlight)
        self.assertTrue(config.live_trading)

    def test_research_soak_mode_forces_non_live_execution(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LIVE_TRADING": "true",
                "LIVE_TRADING_GREENLIGHT": "true",
                "ENABLE_RESEARCH_SOAK_MODE": "true",
            },
            clear=False,
        ):
            config = BotConfig.from_env(force_live=True)

        self.assertTrue(config.live_trading_requested)
        self.assertTrue(config.live_trading_greenlight)
        self.assertTrue(config.enable_research_soak_mode)
        self.assertFalse(config.live_trading)


if __name__ == "__main__":
    unittest.main()
