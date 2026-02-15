from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from ai_trader_bot.learning.runtime_state import RuntimeStateStore


class RuntimeStateTests(unittest.TestCase):
    def test_runtime_state_persists_pull_and_warmup_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "runtime_state.json"
            store = RuntimeStateStore(str(path))

            self.assertIsNone(store.get_last_research_pull_at())
            self.assertFalse(store.is_warmup_done_for_day(date(2026, 2, 16)))

            ts = datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc)
            store.mark_research_pull(ts)
            store.mark_warmup_done_for_day(date(2026, 2, 16))

            reloaded = RuntimeStateStore(str(path))
            self.assertEqual(reloaded.get_last_research_pull_at(), ts)
            self.assertTrue(reloaded.is_warmup_done_for_day(date(2026, 2, 16)))

    def test_runtime_state_tracks_bootstrap_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "runtime_state.json"
            store = RuntimeStateStore(str(path))

            self.assertIsNone(store.get_first_start_date_local())
            self.assertFalse(store.is_bootstrap_complete())

            first = store.ensure_first_start_date_local(date(2026, 2, 10))
            self.assertEqual(first, date(2026, 2, 10))
            store.mark_bootstrap_complete(date(2026, 2, 17))

            reloaded = RuntimeStateStore(str(path))
            self.assertEqual(reloaded.get_first_start_date_local(), date(2026, 2, 10))
            self.assertEqual(reloaded.get_bootstrap_complete_date_local(), date(2026, 2, 17))
            self.assertTrue(reloaded.is_bootstrap_complete())


if __name__ == "__main__":
    unittest.main()
