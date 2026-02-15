from __future__ import annotations

import unittest

from ai_trader_bot.core.config import BotConfig
from ai_trader_bot.app.engine import AutoTrader
from ai_trader_bot.core.models import PortfolioSnapshot, Signal
from ai_trader_bot.learning.ai_interpreter import LLMDecisionPlan


class FakeBroker:
    def __init__(self) -> None:
        self.chain_requests: list[str] = []

    def get_option_chain(self, symbol: str) -> dict:
        self.chain_requests.append(symbol)
        if symbol == "NVDA":
            return {
                "callExpDateMap": {
                    "2026-04-17:30": {
                        "900.0": [
                            {
                                "symbol": "NVDA  260417C00900000",
                                "underlyingSymbol": "NVDA",
                                "strikePrice": 900.0,
                                "daysToExpiration": 30,
                                "delta": 0.45,
                                "bid": 1.10,
                                "ask": 1.20,
                                "mark": 1.15,
                                "totalVolume": 500,
                                "openInterest": 3000,
                            }
                        ]
                    }
                }
            }

        if symbol == "AMD":
            return {
                "callExpDateMap": {
                    "2026-04-17:30": {
                        "190.0": [
                            {
                                "symbol": "AMD  260417C00190000",
                                "underlyingSymbol": "AMD",
                                "strikePrice": 190.0,
                                "daysToExpiration": 30,
                                "delta": 0.42,
                                "bid": 0.95,
                                "ask": 1.00,
                                "mark": 0.98,
                                "totalVolume": 800,
                                "openInterest": 2500,
                            }
                        ]
                    }
                }
            }

        return {}


class EngineOptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = BotConfig(
            universe=["NVDA", "AMD"],
            include_quantum=False,
            option_capital_fraction=0.30,
            option_signal_threshold=0.03,
            option_min_dte=14,
            option_max_dte=45,
            option_target_delta=0.45,
            max_option_contracts=2,
            enable_options=True,
        )
        self.broker = FakeBroker()
        self.trader = AutoTrader(self.config, self.broker)

        self.signals = [
            Signal(
                symbol="NVDA",
                price=900.0,
                momentum_20d=0.10,
                momentum_5d=0.03,
                trend_20d=0.04,
                volatility_20d=0.40,
                news_score=0.25,
                score=0.18,
            ),
            Signal(
                symbol="AMD",
                price=190.0,
                momentum_20d=0.08,
                momentum_5d=0.02,
                trend_20d=0.03,
                volatility_20d=0.35,
                news_score=0.20,
                score=0.12,
            ),
        ]
        self.signals_by_symbol = {signal.symbol: signal for signal in self.signals}

    def test_build_option_orders_opens_multiple_contracts(self) -> None:
        snapshot = PortfolioSnapshot(cash=500.0, equity_positions={}, option_positions={})

        orders = self.trader._build_option_orders(
            snapshot,
            self.signals,
            self.signals_by_symbol,
            account_equity=1000.0,
            estimated_cash=500.0,
        )

        self.assertEqual(len(orders), 2)
        self.assertEqual([order.symbol for order in orders], ["NVDA  260417C00900000", "AMD  260417C00190000"])
        self.assertTrue(all(order.instruction == "BUY_TO_OPEN" for order in orders))

    def test_build_option_orders_skips_existing_underlying(self) -> None:
        snapshot = PortfolioSnapshot(
            cash=500.0,
            equity_positions={},
            option_positions={"NVDA260417C00900000": 1},
        )

        orders = self.trader._build_option_orders(
            snapshot,
            self.signals,
            self.signals_by_symbol,
            account_equity=1000.0,
            estimated_cash=500.0,
        )

        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].symbol, "AMD  260417C00190000")

    def test_blend_news_with_history_uses_weight(self) -> None:
        blended = AutoTrader._blend_news_with_history(
            current_news_score=0.8,
            historical_news_score=0.2,
            history_weight=0.5,
        )
        self.assertAlmostEqual(blended, 0.5, places=6)

    def test_decision_window_lookback_enforces_minimum(self) -> None:
        self.assertEqual(
            AutoTrader._decision_window_lookback(168, 72),
            168,
        )
        self.assertEqual(
            AutoTrader._decision_window_lookback(168, 240),
            240,
        )

    def test_build_orders_uses_llm_plan_and_forced_exit(self) -> None:
        config = BotConfig(
            universe=["NVDA", "META"],
            include_quantum=False,
            enable_llm_first_decisioning=True,
            ai_provider="openai",
            ai_api_key="test-key",
            llm_first_min_confidence=0.2,
            llm_first_require_signals_for_entries=True,
            llm_support_min_signal_score=0.0,
            enable_options=False,
            min_signal_to_enter=0.5,
            signal_to_exit=-0.5,
        )
        trader = AutoTrader(config, self.broker)
        signals = [
            Signal(
                symbol="NVDA",
                price=90.0,
                momentum_20d=0.10,
                momentum_5d=0.03,
                trend_20d=0.04,
                volatility_20d=0.40,
                news_score=0.25,
                score=0.12,
            ),
            Signal(
                symbol="META",
                price=500.0,
                momentum_20d=0.06,
                momentum_5d=0.02,
                trend_20d=0.03,
                volatility_20d=0.22,
                news_score=0.10,
                score=0.09,
            ),
        ]
        snapshot = PortfolioSnapshot(cash=800.0, equity_positions={"META": 2}, option_positions={})
        llm_plan = LLMDecisionPlan(
            equity_buy_symbols=["NVDA"],
            option_buy_symbols=[],
            exit_symbols=["META"],
            confidence=0.9,
            summary="Rotate into NVDA and exit META",
            rationale_by_symbol={"NVDA": "better setup"},
            raw={},
        )

        orders, llm_plan_used = trader._build_orders(snapshot, signals, llm_plan=llm_plan)

        self.assertTrue(llm_plan_used)
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0].symbol, "META")
        self.assertEqual(orders[0].instruction, "SELL")
        self.assertEqual(orders[0].reason, "llm_forced_exit")
        self.assertEqual(orders[1].symbol, "NVDA")
        self.assertEqual(orders[1].instruction, "BUY")


if __name__ == "__main__":
    unittest.main()
