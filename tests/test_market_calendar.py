from __future__ import annotations

import unittest
from datetime import date

from ai_trader_bot.data.market_calendar import is_us_equity_market_day


class MarketCalendarTests(unittest.TestCase):
    def test_weekend_closed(self) -> None:
        self.assertFalse(is_us_equity_market_day(date(2026, 2, 14)))  # Saturday

    def test_regular_weekday_open(self) -> None:
        self.assertTrue(is_us_equity_market_day(date(2026, 2, 18)))  # Wednesday

    def test_thanksgiving_closed(self) -> None:
        self.assertFalse(is_us_equity_market_day(date(2026, 11, 26)))


if __name__ == "__main__":
    unittest.main()
