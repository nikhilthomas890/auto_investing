from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    strike: float
    dte: int
    delta: float | None
    bid: float
    ask: float
    mark: float
    volume: int
    open_interest: int
    premium_per_contract: float


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _extract_dte(expiration_key: str, fallback: int = 0) -> int:
    if ":" not in expiration_key:
        return fallback
    tail = expiration_key.split(":", 1)[1]
    return _to_int(tail, fallback)


def extract_call_contracts(option_chain: dict) -> list[OptionContract]:
    result: list[OptionContract] = []
    call_map = option_chain.get("callExpDateMap")
    if not isinstance(call_map, dict):
        return result

    for expiration_key, strike_map in call_map.items():
        if not isinstance(strike_map, dict):
            continue
        inferred_dte = _extract_dte(str(expiration_key), fallback=0)

        for _, contracts in strike_map.items():
            if not isinstance(contracts, list):
                continue
            for raw in contracts:
                if not isinstance(raw, dict):
                    continue

                symbol = str(raw.get("symbol") or "").strip()
                if not symbol:
                    continue

                bid = _to_float(raw.get("bid"))
                ask = _to_float(raw.get("ask"))
                mark = _to_float(raw.get("mark"))
                delta_raw = raw.get("delta")
                delta = _to_float(delta_raw) if delta_raw is not None else None
                dte = _to_int(raw.get("daysToExpiration"), inferred_dte)
                strike = _to_float(raw.get("strikePrice"))
                volume = _to_int(raw.get("totalVolume"))
                open_interest = _to_int(raw.get("openInterest"))
                underlying = str(raw.get("underlyingSymbol") or "").strip() or option_underlying(symbol)

                basis = ask if ask > 0 else (mark if mark > 0 else bid)
                if basis <= 0:
                    continue

                result.append(
                    OptionContract(
                        symbol=symbol,
                        underlying=underlying,
                        strike=strike,
                        dte=dte,
                        delta=delta,
                        bid=bid,
                        ask=ask,
                        mark=mark,
                        volume=volume,
                        open_interest=open_interest,
                        premium_per_contract=basis * 100.0,
                    )
                )

    return result


def choose_bullish_call(
    option_chain: dict,
    *,
    max_premium_dollars: float,
    min_dte: int,
    max_dte: int,
    target_delta: float,
) -> OptionContract | None:
    candidates = extract_call_contracts(option_chain)
    filtered: list[tuple[float, OptionContract]] = []

    for contract in candidates:
        if contract.dte < min_dte or contract.dte > max_dte:
            continue
        if contract.premium_per_contract > max_premium_dollars:
            continue

        if contract.delta is not None:
            abs_delta = abs(contract.delta)
            if abs_delta < 0.20 or abs_delta > 0.70:
                continue
        else:
            abs_delta = target_delta

        spread = (contract.ask - contract.bid) if contract.ask > 0 and contract.bid > 0 else contract.ask
        liquidity_bonus = 0.0005 * contract.open_interest + 0.0002 * contract.volume

        quality = abs(abs_delta - target_delta) + (0.03 * max(spread, 0.0)) - liquidity_bonus
        filtered.append((quality, contract))

    if not filtered:
        return None

    filtered.sort(key=lambda item: item[0])
    return filtered[0][1]


def option_underlying(option_symbol: str) -> str:
    clean = option_symbol.strip().upper()
    head = clean.split(" ", 1)[0].strip()
    if head.isalpha():
        return head

    letters: list[str] = []
    for char in head:
        if char.isalpha():
            letters.append(char)
            continue
        break

    return "".join(letters) or head
