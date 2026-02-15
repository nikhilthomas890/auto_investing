from __future__ import annotations

import logging
from typing import Any

from ..core.config import BotConfig
from ..core.models import PortfolioSnapshot, TradeOrder

_SAFE_SCHWAB_CLIENT_METHODS = {
    "set_timeout",
    "get_account_numbers",
    "get_account",
    "get_quote",
    "get_price_history_every_day",
    "get_option_chain",
    "place_order",
}

_SAFE_SCHWAB_CLIENT_ATTRIBUTES = {
    "Account",
}


class _RestrictedSchwabClient:
    def __init__(self, client: Any, *, restrictions_enabled: bool) -> None:
        self._client = client
        self._restrictions_enabled = restrictions_enabled

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if not self._restrictions_enabled:
            return target

        if name in _SAFE_SCHWAB_CLIENT_ATTRIBUTES:
            return target

        if callable(target):
            if name not in _SAFE_SCHWAB_CLIENT_METHODS:
                raise RuntimeError(
                    "Blocked Schwab API method "
                    f"'{name}'. Money-transfer and non-trading endpoints are disabled in this bot."
                )
            return target

        raise RuntimeError(
            "Blocked Schwab API attribute "
            f"'{name}'. Only trading and market-data endpoints are allowed."
        )


class SchwabBroker:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.live_trading = config.live_trading

        try:
            from schwab.auth import easy_client

            self._easy_client = easy_client
        except Exception as exc:
            raise RuntimeError(
                "schwab-py is required. Install dependencies first: pip install -r requirements.txt"
            ) from exc

        if not config.schwab_api_key or not config.schwab_app_secret:
            raise RuntimeError(
                "Missing SCHWAB_API_KEY or SCHWAB_APP_SECRET in environment. "
                "Set credentials before starting the bot."
            )

        raw_client = self._easy_client(
            api_key=config.schwab_api_key,
            app_secret=config.schwab_app_secret,
            callback_url=config.schwab_callback_url,
            token_path=config.schwab_token_path,
        )
        if not config.restrict_fund_transfers:
            raise RuntimeError(
                "Safety policy violation: RESTRICT_FUND_TRANSFERS must be true. "
                "This bot is not permitted to use money-transfer endpoints."
            )
        self.client = _RestrictedSchwabClient(
            raw_client,
            restrictions_enabled=True,
        )
        self.client.set_timeout(config.request_timeout_seconds)
        logging.info("Schwab money-transfer restrictions are enabled (trading/market-data only).")
        self.account_hash = self._resolve_account_hash(config.schwab_account_number)

    def _resolve_account_hash(self, target_account_number: str | None) -> str:
        response = self.client.get_account_numbers()
        response.raise_for_status()
        payload = response.json()
        if not payload:
            raise RuntimeError("No Schwab accounts were returned by the API.")

        if target_account_number:
            for account in payload:
                if str(account.get("accountNumber", "")).strip() == target_account_number:
                    hash_value = str(account.get("hashValue", "")).strip()
                    if hash_value:
                        return hash_value
            raise RuntimeError(
                f"Could not find SCHWAB_ACCOUNT_NUMBER={target_account_number} in linked Schwab accounts."
            )

        default_hash = str(payload[0].get("hashValue", "")).strip()
        if not default_hash:
            raise RuntimeError("Schwab account hash was missing from API response.")
        return default_hash

    def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        try:
            fields = [self.client.Account.Fields.POSITIONS]
            response = self.client.get_account(self.account_hash, fields=fields)
        except Exception:
            response = self.client.get_account(self.account_hash)

        response.raise_for_status()
        payload = response.json()

        account = payload.get("securitiesAccount") or payload.get("account") or payload
        balances = account.get("currentBalances") or account.get("initialBalances") or {}
        cash = self._first_number(
            balances,
            [
                "cashAvailableForTrading",
                "cashBalance",
                "availableFunds",
                "buyingPower",
            ],
            fallback=self.config.starting_capital,
        )

        equity_positions: dict[str, int] = {}
        option_positions: dict[str, int] = {}

        for position in account.get("positions", []) or []:
            instrument = position.get("instrument") or {}
            symbol = str(instrument.get("symbol") or "").strip().upper()
            if not symbol:
                continue

            asset_type = str(instrument.get("assetType") or "").strip().upper()
            long_qty = float(position.get("longQuantity") or 0.0)
            short_qty = float(position.get("shortQuantity") or 0.0)
            net_qty = int(round(long_qty - short_qty))
            if net_qty == 0:
                continue

            if asset_type == "OPTION":
                option_positions[symbol] = net_qty
            else:
                equity_positions[symbol] = net_qty

        return PortfolioSnapshot(cash=max(cash, 0.0), equity_positions=equity_positions, option_positions=option_positions)

    def get_last_price(self, symbol: str) -> float | None:
        response = self.client.get_quote(symbol)
        response.raise_for_status()
        payload = response.json()

        entry = payload.get(symbol) or payload.get(symbol.upper())
        if entry is None and payload:
            entry = next(iter(payload.values()))
        if not isinstance(entry, dict):
            return None

        quote_block = entry.get("quote") if isinstance(entry.get("quote"), dict) else entry

        for key in ("lastPrice", "mark", "closePrice", "bidPrice", "askPrice"):
            price = quote_block.get(key)
            if isinstance(price, (int, float)) and price > 0:
                return float(price)

        return None

    def get_history(self, symbol: str, days: int) -> list[float]:
        response = self.client.get_price_history_every_day(symbol)
        response.raise_for_status()
        payload = response.json()

        candles = payload.get("candles") or []
        closes: list[float] = []
        for candle in candles:
            close = candle.get("close")
            if isinstance(close, (int, float)) and close > 0:
                closes.append(float(close))

        return closes[-days:] if days > 0 else closes

    def get_option_chain(self, symbol: str) -> dict[str, Any]:
        response = self.client.get_option_chain(symbol)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        return payload

    def place_order(self, order: TradeOrder) -> dict[str, Any]:
        if order.quantity <= 0:
            return {"status": "ignored", "reason": "non_positive_quantity"}

        if not self.live_trading:
            logging.info("DRY-RUN ORDER: %s", order)
            return {"status": "simulated", "order": order}

        order_spec = self._build_order_spec(order)
        response = self.client.place_order(self.account_hash, order_spec)

        if response.status_code >= 400:
            details = ""
            try:
                details = str(response.json())
            except Exception:
                details = response.text
            raise RuntimeError(f"Schwab order rejected ({response.status_code}): {details}")

        return {
            "status": "submitted",
            "http_status": response.status_code,
            "location": response.headers.get("Location"),
        }

    def _build_order_spec(self, order: TradeOrder) -> dict[str, Any]:
        if order.asset_type == "EQUITY":
            return self._build_equity_spec(order)
        if order.asset_type == "OPTION":
            return self._build_option_spec(order)
        raise RuntimeError(f"Unsupported asset type: {order.asset_type}")

    def _build_equity_spec(self, order: TradeOrder) -> dict[str, Any]:
        from schwab.orders.equities import (
            equity_buy_limit,
            equity_buy_market,
            equity_sell_limit,
            equity_sell_market,
        )

        side = order.instruction.upper()
        if side == "BUY":
            builder = (
                equity_buy_limit(order.symbol, order.quantity, order.limit_price)
                if order.limit_price is not None
                else equity_buy_market(order.symbol, order.quantity)
            )
            return builder.build()

        if side == "SELL":
            builder = (
                equity_sell_limit(order.symbol, order.quantity, order.limit_price)
                if order.limit_price is not None
                else equity_sell_market(order.symbol, order.quantity)
            )
            return builder.build()

        raise RuntimeError(f"Unsupported equity instruction: {order.instruction}")

    def _build_option_spec(self, order: TradeOrder) -> dict[str, Any]:
        from schwab.orders.options import (
            option_buy_to_open_limit,
            option_buy_to_open_market,
            option_sell_to_close_limit,
            option_sell_to_close_market,
        )

        side = order.instruction.upper()
        if side == "BUY_TO_OPEN":
            builder = (
                option_buy_to_open_limit(order.symbol, order.quantity, order.limit_price)
                if order.limit_price is not None
                else option_buy_to_open_market(order.symbol, order.quantity)
            )
            return builder.build()

        if side == "SELL_TO_CLOSE":
            builder = (
                option_sell_to_close_limit(order.symbol, order.quantity, order.limit_price)
                if order.limit_price is not None
                else option_sell_to_close_market(order.symbol, order.quantity)
            )
            return builder.build()

        raise RuntimeError(f"Unsupported option instruction: {order.instruction}")

    @staticmethod
    def _first_number(container: dict[str, Any], keys: list[str], fallback: float) -> float:
        for key in keys:
            value = container.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return fallback
