from __future__ import annotations

import unittest

from ai_trader_bot.strategy.options import choose_bullish_call, option_underlying


class OptionTests(unittest.TestCase):
    def test_choose_bullish_call_filters_by_premium_and_dte(self) -> None:
        chain = {
            "callExpDateMap": {
                "2026-04-17:62": {
                    "100.0": [
                        {
                            "symbol": "NVDA  260417C00100000",
                            "underlyingSymbol": "NVDA",
                            "strikePrice": 100.0,
                            "daysToExpiration": 62,
                            "delta": 0.40,
                            "bid": 3.80,
                            "ask": 4.00,
                            "mark": 3.90,
                            "totalVolume": 200,
                            "openInterest": 1200,
                        }
                    ],
                    "110.0": [
                        {
                            "symbol": "NVDA  260417C00110000",
                            "underlyingSymbol": "NVDA",
                            "strikePrice": 110.0,
                            "daysToExpiration": 62,
                            "delta": 0.30,
                            "bid": 1.10,
                            "ask": 1.20,
                            "mark": 1.15,
                            "totalVolume": 400,
                            "openInterest": 2000,
                        }
                    ],
                }
            }
        }

        chosen = choose_bullish_call(
            chain,
            max_premium_dollars=150.0,
            min_dte=21,
            max_dte=90,
            target_delta=0.40,
        )

        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen.symbol, "NVDA  260417C00110000")

    def test_option_underlying_parses_space_delimited_symbol(self) -> None:
        self.assertEqual(option_underlying("MSFT  260417C00400000"), "MSFT")

    def test_option_underlying_parses_occ_symbol(self) -> None:
        self.assertEqual(option_underlying("AAPL260117C00200000"), "AAPL")


if __name__ == "__main__":
    unittest.main()
