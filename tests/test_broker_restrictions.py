from __future__ import annotations

import unittest

from ai_trader_bot.execution.broker import _RestrictedSchwabClient


class _FakeAccountFields:
    POSITIONS = "positions"


class _FakeAccount:
    Fields = _FakeAccountFields


class _FakeSchwabClient:
    Account = _FakeAccount

    def get_quote(self, symbol: str) -> str:
        return f"quote:{symbol}"

    def transfer_money(self) -> str:
        return "moved"

    def some_other_method(self) -> str:
        return "other"


class BrokerRestrictionTests(unittest.TestCase):
    def test_restrictions_allow_safe_market_data_calls(self) -> None:
        client = _RestrictedSchwabClient(_FakeSchwabClient(), restrictions_enabled=True)
        self.assertEqual(client.get_quote("NVDA"), "quote:NVDA")
        self.assertEqual(client.Account.Fields.POSITIONS, "positions")

    def test_restrictions_block_transfer_method(self) -> None:
        client = _RestrictedSchwabClient(_FakeSchwabClient(), restrictions_enabled=True)
        with self.assertRaises(RuntimeError):
            client.transfer_money()

    def test_restrictions_block_unknown_method(self) -> None:
        client = _RestrictedSchwabClient(_FakeSchwabClient(), restrictions_enabled=True)
        with self.assertRaises(RuntimeError):
            client.some_other_method()

    def test_restrictions_can_be_disabled_explicitly(self) -> None:
        client = _RestrictedSchwabClient(_FakeSchwabClient(), restrictions_enabled=False)
        self.assertEqual(client.transfer_money(), "moved")


if __name__ == "__main__":
    unittest.main()
