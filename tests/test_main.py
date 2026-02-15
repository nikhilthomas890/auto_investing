from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from ai_trader_bot.core.config import BotConfig
from ai_trader_bot.app.main import _bootstrap_context
from ai_trader_bot.learning.runtime_state import RuntimeStateStore


class MainBootstrapTests(unittest.TestCase):
    def test_bootstrap_waits_until_next_market_day_after_day_five(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state = RuntimeStateStore(str(Path(tmp_dir) / "runtime_state.json"))
            config = BotConfig(
                enable_first_run_bootstrap=True,
                first_run_bootstrap_days=5,
            )

            # First start is Monday 2026-01-05, +5 days is Saturday 2026-01-10.
            first = _bootstrap_context(
                config,
                state,
                local_day=date(2026, 1, 5),
                is_market_day=True,
            )
            self.assertTrue(bool(first.get("active", False)))
            self.assertEqual(first.get("trade_enable_date_local"), "2026-01-12")

            # Sunday still active.
            sunday = _bootstrap_context(
                config,
                state,
                local_day=date(2026, 1, 11),
                is_market_day=False,
            )
            self.assertTrue(bool(sunday.get("active", False)))

            # Monday flips complete and disables bootstrap.
            monday = _bootstrap_context(
                config,
                state,
                local_day=date(2026, 1, 12),
                is_market_day=True,
            )
            self.assertFalse(bool(monday.get("active", True)))


if __name__ == "__main__":
    unittest.main()

