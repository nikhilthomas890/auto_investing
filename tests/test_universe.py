from __future__ import annotations

import unittest

from ai_trader_bot.data.universe import build_theme_map


class UniverseTests(unittest.TestCase):
    def test_space_symbol_gets_custom_query(self) -> None:
        theme_map = build_theme_map(["RKLB"], include_quantum=False)
        self.assertIn("RKLB", theme_map)
        self.assertIn("Rocket Lab", theme_map["RKLB"])

    def test_fallback_query_mentions_space(self) -> None:
        theme_map = build_theme_map(["TEST"], include_quantum=False)
        self.assertIn("space", theme_map["TEST"].lower())


if __name__ == "__main__":
    unittest.main()
