from __future__ import annotations

import unittest

from ai_trader_bot.strategy.signals import compute_signal


class SignalTests(unittest.TestCase):
    def test_compute_signal_returns_value_for_valid_series(self) -> None:
        closes = [100 + idx for idx in range(30)]
        signal = compute_signal("NVDA", 130.0, closes, news_score=0.4)

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.symbol, "NVDA")
        self.assertGreater(signal.score, -1.0)

    def test_compute_signal_rejects_short_series(self) -> None:
        closes = [100 + idx for idx in range(10)]
        signal = compute_signal("NVDA", 110.0, closes, news_score=0.0)
        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main()
