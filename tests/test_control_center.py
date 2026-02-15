from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_trader_bot.control import DecisionControlCenter
from ai_trader_bot.core.config import BotConfig


class ControlCenterTests(unittest.TestCase):
    def _config(self, base: Path) -> BotConfig:
        return BotConfig(
            control_actions_log_path=str(base / "control_actions.jsonl"),
            control_results_log_path=str(base / "control_results.jsonl"),
            runtime_overrides_path=str(base / "runtime_overrides.json"),
            model_build_requests_path=str(base / "model_build_requests.jsonl"),
        )

    def test_set_config_action_applies_and_persists_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            config = self._config(base)
            center = DecisionControlCenter(config)

            center.submit_action(
                action_type="set_config",
                payload={"key": "rebalance_interval_seconds", "value": "420"},
            )
            outcome = center.process_pending_actions()
            self.assertEqual(outcome["processed"], 1)
            self.assertEqual(config.rebalance_interval_seconds, 420)

            overrides_payload = json.loads(Path(config.runtime_overrides_path).read_text(encoding="utf-8"))
            self.assertEqual(overrides_payload.get("rebalance_interval_seconds"), 420)

            reloaded_config = self._config(base)
            reloaded_center = DecisionControlCenter(reloaded_config)
            self.assertEqual(reloaded_config.rebalance_interval_seconds, 420)
            keys = {row["key"] for row in reloaded_center.list_configurable_keys()}
            self.assertIn("rebalance_interval_seconds", keys)

    def test_protected_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            config = self._config(base)
            center = DecisionControlCenter(config)

            center.submit_action(
                action_type="set_config",
                payload={"key": "restrict_fund_transfers", "value": False},
            )
            outcome = center.process_pending_actions()
            self.assertEqual(outcome["processed"], 1)
            result = outcome["outcomes"][0]
            self.assertEqual(result["status"], "rejected")
            self.assertTrue(config.restrict_fund_transfers)

            keys = {row["key"] for row in center.list_configurable_keys()}
            self.assertNotIn("restrict_fund_transfers", keys)

    def test_live_trading_keys_are_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            config = self._config(base)
            center = DecisionControlCenter(config)

            center.submit_action(
                action_type="set_config",
                payload={"key": "live_trading", "value": True},
            )
            outcome = center.process_pending_actions()
            self.assertEqual(outcome["processed"], 1)
            result = outcome["outcomes"][0]
            self.assertEqual(result["status"], "rejected")
            self.assertFalse(config.live_trading)

            keys = {row["key"] for row in center.list_configurable_keys()}
            self.assertNotIn("live_trading", keys)
            self.assertNotIn("live_trading_requested", keys)
            self.assertNotIn("live_trading_greenlight", keys)

    def test_new_model_request_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            config = self._config(base)
            center = DecisionControlCenter(config)

            center.submit_action(
                action_type="new_model_request",
                payload={
                    "model_name": "policy-regime-shift-model",
                    "rationale": "Capture cross-asset policy shocks",
                    "target_quarter": "2026-Q3",
                },
            )
            outcome = center.process_pending_actions()
            self.assertEqual(outcome["processed"], 1)
            self.assertTrue(outcome["deploy_recommended"])
            result = outcome["outcomes"][0]
            self.assertEqual(result["status"], "queued")

            lines = [
                json.loads(line)
                for line in Path(config.model_build_requests_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["event"], "model_build_request")
            self.assertEqual(lines[0]["model_name"], "policy-regime-shift-model")


if __name__ == "__main__":
    unittest.main()
