from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_trader_bot.core.config import BotConfig
from ai_trader_bot.learning.ai_interpreter import (
    LongTermMemoryStore,
    OpenAIDecisionPlanner,
    _extract_json,
)


class AIInterpreterTests(unittest.TestCase):
    def test_extract_json_handles_wrapped_response(self) -> None:
        payload = _extract_json("Result: {\"short_term\":0.2,\"long_term\":-0.1,\"confidence\":0.8}")
        self.assertIn("short_term", payload)
        self.assertEqual(payload["confidence"], 0.8)

    def test_long_term_memory_blends_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "state.json"
            store = LongTermMemoryStore(str(state_path), alpha=0.5)
            first = store.update("NVDA", 0.8)
            second = store.update("NVDA", 0.0)

            self.assertAlmostEqual(first, 0.8, places=6)
            self.assertAlmostEqual(second, 0.4, places=6)

            reloaded = LongTermMemoryStore(str(state_path), alpha=0.5)
            self.assertAlmostEqual(reloaded.get("NVDA"), 0.4, places=6)

    def test_price_feedback_penalizes_wrong_direction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "state.json"
            store = LongTermMemoryStore(str(state_path), alpha=0.2)
            store.update("NVDA", 0.5)
            store.record_prediction("NVDA", prediction_score=0.8, reference_price=100.0)

            adjustment = store.apply_price_feedback("NVDA", current_price=90.0, strength=0.5)
            self.assertLess(adjustment, 0.0)
            self.assertLess(store.get("NVDA"), 0.5)

    def test_llm_decision_planner_disables_without_api_key(self) -> None:
        config = BotConfig(enable_llm_first_decisioning=True, ai_provider="openai", ai_api_key="")
        planner = OpenAIDecisionPlanner(config)
        self.assertFalse(planner.enabled)
        plan = planner.build_plan(
            symbol_contexts=[{"symbol": "NVDA", "score": 0.1}],
            held_equities=[],
            held_option_underlyings=[],
        )
        self.assertIsNone(plan)

    def test_llm_decision_planner_builds_normalized_plan(self) -> None:
        config = BotConfig(
            enable_llm_first_decisioning=True,
            ai_provider="openai",
            ai_api_key="test-key",
            llm_first_max_symbols=5,
        )
        planner = OpenAIDecisionPlanner(config)
        self.assertTrue(planner.enabled)

        with patch(
            "ai_trader_bot.learning.ai_interpreter._openai_json_response",
            return_value={
                "equity_buy_symbols": ["nvda", "NVDA", "MSFT"],
                "option_buy_symbols": ["amd"],
                "exit_symbols": ["meta"],
                "confidence": 0.81,
                "summary": "High-conviction rotation",
                "rationale_by_symbol": {"NVDA": "accelerating demand", "MSFT": "cloud AI strength"},
            },
        ):
            plan = planner.build_plan(
                symbol_contexts=[
                    {"symbol": "NVDA", "score": 0.22},
                    {"symbol": "MSFT", "score": 0.18},
                    {"symbol": "AMD", "score": 0.15},
                    {"symbol": "META", "score": 0.05},
                ],
                held_equities=["META"],
                held_option_underlyings=["AMD"],
            )

        self.assertIsNotNone(plan)
        if plan is None:
            self.fail("Expected non-null plan")
        self.assertEqual(plan.equity_buy_symbols, ["NVDA", "MSFT"])
        self.assertEqual(plan.option_buy_symbols, ["AMD"])
        self.assertEqual(plan.exit_symbols, ["META"])
        self.assertAlmostEqual(plan.confidence, 0.81, places=6)
        self.assertIn("NVDA", plan.rationale_by_symbol)


if __name__ == "__main__":
    unittest.main()
