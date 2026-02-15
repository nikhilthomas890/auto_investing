from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.models import Signal

FEATURE_KEYS = [
    "momentum_20d",
    "momentum_5d",
    "trend_20d",
    "news_score",
    "macro_score",
    "ai_short_term",
    "ai_long_term",
    "volatility_risk",
]

DEFAULT_SOURCE_TYPES = [
    "news",
    "sec_filing",
    "earnings_transcript",
    "social",
    "analyst_rating",
    "unknown",
]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def signal_feature_profile(
    signal: Signal,
    *,
    ai_short_term_weight: float,
    ai_long_term_weight: float,
    macro_weight: float = 0.0,
) -> dict[str, float]:
    return {
        "momentum_20d": 0.45 * signal.momentum_20d,
        "momentum_5d": 0.20 * signal.momentum_5d,
        "trend_20d": 0.20 * signal.trend_20d,
        "news_score": 0.25 * signal.news_score,
        "macro_score": max(0.0, macro_weight) * signal.macro_score,
        "ai_short_term": ai_short_term_weight * signal.ai_short_term_score,
        "ai_long_term": ai_long_term_weight * signal.ai_long_term_score,
        "volatility_risk": 0.15 * min(max(signal.volatility_20d, 0.0), 1.0),
    }


def call_rationale(feature_profile: dict[str, float], *, max_items: int = 3) -> list[dict[str, float | str]]:
    ranked: list[tuple[str, float]] = []
    for key, value in feature_profile.items():
        if key == "volatility_risk":
            continue
        if value <= 0:
            continue
        ranked.append((key, value))

    ranked.sort(key=lambda item: item[1], reverse=True)
    return [{"driver": key, "contribution": value} for key, value in ranked[:max_items]]


def failure_tags(feature_profile: dict[str, float], realized_return: float) -> list[str]:
    tags: list[str] = []
    if realized_return >= 0:
        return tags

    top = call_rationale(feature_profile, max_items=2)
    drivers = {str(item["driver"]) for item in top}

    if "news_score" in drivers:
        tags.append("news_overreaction")
    if "momentum_20d" in drivers or "momentum_5d" in drivers:
        tags.append("momentum_reversal")
    if "ai_short_term" in drivers or "ai_long_term" in drivers:
        tags.append("ai_thesis_miss")
    if "macro_score" in drivers:
        tags.append("macro_policy_miss")

    volatility = float(feature_profile.get("volatility_risk", 0.0))
    if volatility > 0.09:
        tags.append("high_volatility_regime")

    if not tags:
        tags.append("general_prediction_error")

    return tags


class DecisionLearningStore:
    def __init__(
        self,
        *,
        state_path: str,
        journal_path: str,
        evaluation_horizon_hours: int,
        bad_call_return_threshold: float,
        good_call_return_threshold: float,
        learning_rate: float,
        max_feature_penalty: float,
        enable_source_priority_learning: bool = True,
        source_learning_rate: float = 0.18,
        max_source_bias: float = 0.80,
        market_reaction_strength: float = 0.35,
    ) -> None:
        self.state_path = Path(state_path)
        self.journal_path = Path(journal_path)
        self.evaluation_horizon = timedelta(hours=max(1, evaluation_horizon_hours))
        self.bad_call_return_threshold = float(bad_call_return_threshold)
        self.good_call_return_threshold = float(good_call_return_threshold)
        self.learning_rate = _clamp(float(learning_rate), 0.0, 1.0)
        self.max_feature_penalty = max(0.0, float(max_feature_penalty))
        self.enable_source_priority_learning = bool(enable_source_priority_learning)
        self.source_learning_rate = _clamp(float(source_learning_rate), 0.0, 1.0)
        self.max_source_bias = max(0.0, float(max_source_bias))
        self.market_reaction_strength = _clamp(float(market_reaction_strength), 0.0, 1.0)

        self.feature_penalties: dict[str, float] = {key: 0.0 for key in FEATURE_KEYS}
        self.source_bias: dict[str, float] = {key: 0.0 for key in DEFAULT_SOURCE_TYPES}
        self.open_calls: dict[str, dict[str, Any]] = {}
        self.market_observations: dict[str, dict[str, Any]] = {}

        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return

        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(payload, dict):
            return

        penalties = payload.get("feature_penalties")
        if isinstance(penalties, dict):
            for key in FEATURE_KEYS:
                value = penalties.get(key)
                if isinstance(value, (int, float)):
                    self.feature_penalties[key] = _clamp(float(value), 0.0, self.max_feature_penalty)

        source_bias = payload.get("source_bias")
        if isinstance(source_bias, dict):
            for key, value in source_bias.items():
                if not isinstance(key, str) or not isinstance(value, (int, float)):
                    continue
                clean = key.strip().lower()
                if not clean:
                    continue
                self.source_bias[clean] = _clamp(float(value), -self.max_source_bias, self.max_source_bias)

        open_calls = payload.get("open_calls")
        if isinstance(open_calls, dict):
            normalized: dict[str, dict[str, Any]] = {}
            for symbol, call in open_calls.items():
                if not isinstance(symbol, str) or not isinstance(call, dict):
                    continue
                normalized[symbol.upper()] = call
            self.open_calls = normalized

        observations = payload.get("market_observations")
        if isinstance(observations, dict):
            normalized_observations: dict[str, dict[str, Any]] = {}
            for symbol, row in observations.items():
                if not isinstance(symbol, str) or not isinstance(row, dict):
                    continue
                normalized_observations[symbol.upper()] = row
            self.market_observations = normalized_observations

    def _save(self) -> None:
        payload = {
            "feature_penalties": self.feature_penalties,
            "source_bias": self.source_bias,
            "source_learning_enabled": self.enable_source_priority_learning,
            "open_calls": self.open_calls,
            "market_observations": self.market_observations,
            "updated_at": _now_utc().isoformat(),
        }
        try:
            if self.state_path.parent != Path("."):
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logging.warning("Failed writing learning state %s: %s", self.state_path, exc)

    def _append_journal(self, event: dict[str, Any]) -> None:
        try:
            if self.journal_path.parent != Path("."):
                self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
        except Exception as exc:
            logging.warning("Failed writing decision journal %s: %s", self.journal_path, exc)

    def adjustment_for(self, feature_profile: dict[str, float]) -> float:
        adjustment = 0.0
        for key, penalty in self.feature_penalties.items():
            value = float(feature_profile.get(key, 0.0))
            if key == "volatility_risk":
                exposure = max(0.0, value)
            else:
                exposure = max(0.0, value)
            adjustment -= penalty * exposure
        return adjustment

    def source_multiplier_for(self, source_type: str) -> float:
        key = source_type.strip().lower() or "unknown"
        bias = float(self.source_bias.get(key, 0.0))
        # Bias maps to a magnitude multiplier for source sentiment.
        return _clamp(1.0 + bias, 0.25, 2.0)

    def source_multipliers_for(self, source_types: list[str]) -> dict[str, float]:
        result: dict[str, float] = {}
        for source_type in source_types:
            key = source_type.strip().lower() or "unknown"
            result[key] = self.source_multiplier_for(key)
        return result

    @staticmethod
    def _normalize_source_profile(
        source_profile: dict[str, dict[str, float | int]] | dict[str, Any] | None,
    ) -> dict[str, dict[str, float]]:
        if not isinstance(source_profile, dict):
            return {}

        normalized: dict[str, dict[str, float]] = {}
        for raw_key, raw_row in source_profile.items():
            if not isinstance(raw_key, str) or not isinstance(raw_row, dict):
                continue
            key = raw_key.strip().lower() or "unknown"

            sentiment_raw = raw_row.get("sentiment")
            count_raw = raw_row.get("count")
            multiplier_raw = raw_row.get("multiplier", 1.0)

            sentiment = float(sentiment_raw) if isinstance(sentiment_raw, (int, float)) else 0.0
            count = float(count_raw) if isinstance(count_raw, (int, float)) else 0.0
            multiplier = float(multiplier_raw) if isinstance(multiplier_raw, (int, float)) else 1.0
            if count <= 0:
                continue

            normalized[key] = {
                "sentiment": _clamp(sentiment, -1.0, 1.0),
                "count": max(0.0, count),
                "multiplier": _clamp(multiplier, 0.1, 3.0),
            }

        return normalized

    def _update_source_bias(
        self,
        source_profile: dict[str, dict[str, float]],
        *,
        realized_return: float,
        channel_weight: float,
    ) -> dict[str, float]:
        if not self.enable_source_priority_learning or not source_profile:
            return {}

        realized_signal = _clamp(realized_return / 0.05, -2.0, 2.0)
        if realized_signal == 0:
            return {}

        total_count = sum(max(0.0, row.get("count", 0.0)) for row in source_profile.values())
        if total_count <= 0:
            return {}

        changed: dict[str, float] = {}
        for source_type, row in source_profile.items():
            sentiment = _clamp(float(row.get("sentiment", 0.0)), -1.0, 1.0)
            count = max(0.0, float(row.get("count", 0.0)))
            if count <= 0 or sentiment == 0:
                continue

            count_share = count / total_count
            influence = min(1.0, abs(sentiment)) * count_share
            delta = (
                self.source_learning_rate
                * channel_weight
                * realized_signal
                * sentiment
                * influence
            )
            if delta == 0:
                continue

            before = float(self.source_bias.get(source_type, 0.0))
            after = _clamp(before + delta, -self.max_source_bias, self.max_source_bias)
            if after != before:
                self.source_bias[source_type] = after
                changed[source_type] = after - before

        return changed

    def update_from_market_reaction(
        self,
        *,
        symbol: str,
        current_price: float,
        source_profile: dict[str, dict[str, float | int]] | None,
    ) -> dict[str, Any] | None:
        if current_price <= 0:
            return None
        if not self.enable_source_priority_learning:
            return None

        key = symbol.strip().upper()
        if not key:
            return None

        normalized_profile = self._normalize_source_profile(source_profile)
        now = _now_utc()
        prior = self.market_observations.get(key)

        event: dict[str, Any] | None = None
        if isinstance(prior, dict):
            prev_price = prior.get("price")
            prev_profile = self._normalize_source_profile(
                prior.get("source_profile") if isinstance(prior.get("source_profile"), dict) else {}
            )
            if isinstance(prev_price, (int, float)) and float(prev_price) > 0 and prev_profile:
                realized_return = (current_price / float(prev_price)) - 1.0
                source_updates = self._update_source_bias(
                    prev_profile,
                    realized_return=realized_return,
                    channel_weight=self.market_reaction_strength,
                )
                if source_updates:
                    event = {
                        "event": "source_market_reaction_updated",
                        "timestamp": now.isoformat(),
                        "symbol": key,
                        "reference_price": float(prev_price),
                        "current_price": float(current_price),
                        "realized_return": realized_return,
                        "source_update": source_updates,
                        "source_bias_after": self.source_bias,
                    }
                    self._append_journal(event)

        self.market_observations[key] = {
            "timestamp": now.isoformat(),
            "price": float(current_price),
            "source_profile": normalized_profile,
        }
        self._save()
        return event

    def maybe_record_call(
        self,
        *,
        signal: Signal,
        feature_profile: dict[str, float],
        source_profile: dict[str, dict[str, float | int]] | None = None,
        entry_threshold: float,
        option_threshold: float,
    ) -> None:
        if signal.score < min(entry_threshold, option_threshold):
            return

        symbol = signal.symbol.upper()
        existing = self.open_calls.get(symbol)
        if existing is not None:
            return

        created_at = _now_utc()
        reason = call_rationale(feature_profile)
        normalized_source_profile = self._normalize_source_profile(source_profile)
        call = {
            "id": str(uuid.uuid4()),
            "symbol": symbol,
            "created_at": created_at.isoformat(),
            "entry_price": signal.price,
            "signal_score": signal.score,
            "feature_profile": feature_profile,
            "source_profile": normalized_source_profile,
            "rationale": reason,
            "kind": "options_focus" if signal.score >= option_threshold else "equity_focus",
        }
        self.open_calls[symbol] = call
        self._save()

        self._append_journal(
            {
                "event": "decision_call_opened",
                "timestamp": created_at.isoformat(),
                "symbol": symbol,
                "call_id": call["id"],
                "signal_score": signal.score,
                "entry_price": signal.price,
                "why_call_made": reason,
                "feature_profile": feature_profile,
                "current_feature_penalties": self.feature_penalties,
                "source_profile": normalized_source_profile,
                "current_source_bias": self.source_bias,
            }
        )

    def maybe_resolve_call(self, *, symbol: str, current_price: float) -> dict[str, Any] | None:
        if current_price <= 0:
            return None

        key = symbol.upper()
        call = self.open_calls.get(key)
        if call is None:
            return None

        created_at_raw = call.get("created_at")
        if not isinstance(created_at_raw, str):
            self.open_calls.pop(key, None)
            self._save()
            return None

        try:
            created_at = datetime.fromisoformat(created_at_raw)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except Exception:
            self.open_calls.pop(key, None)
            self._save()
            return None

        now = _now_utc()
        if now - created_at < self.evaluation_horizon:
            return None

        entry_price = call.get("entry_price")
        if not isinstance(entry_price, (int, float)) or float(entry_price) <= 0:
            self.open_calls.pop(key, None)
            self._save()
            return None

        realized_return = (current_price / float(entry_price)) - 1.0
        feature_profile = call.get("feature_profile") if isinstance(call.get("feature_profile"), dict) else {}
        source_profile = self._normalize_source_profile(
            call.get("source_profile") if isinstance(call.get("source_profile"), dict) else {}
        )

        outcome = "neutral"
        if realized_return <= self.bad_call_return_threshold:
            outcome = "bad_call"
        elif realized_return >= self.good_call_return_threshold:
            outcome = "good_call"

        update_summary = self._update_penalties(feature_profile, realized_return, outcome)
        source_update_summary = self._update_source_bias(
            source_profile,
            realized_return=realized_return,
            channel_weight=1.0,
        )

        event = {
            "event": "decision_call_resolved",
            "timestamp": now.isoformat(),
            "symbol": key,
            "call_id": call.get("id"),
            "opened_at": created_at.isoformat(),
            "evaluation_hours": (now - created_at).total_seconds() / 3600.0,
            "entry_price": float(entry_price),
            "resolved_price": float(current_price),
            "realized_return": realized_return,
            "outcome": outcome,
            "why_call_made": call.get("rationale") or [],
            "why_bad": failure_tags(feature_profile, realized_return) if outcome == "bad_call" else [],
            "penalty_update": update_summary,
            "source_priority_update": source_update_summary,
            "feature_profile": feature_profile,
            "source_profile": source_profile,
            "feature_penalties_after": self.feature_penalties,
            "source_bias_after": self.source_bias,
        }

        self._append_journal(event)

        self.open_calls.pop(key, None)
        self._save()
        return event

    def _update_penalties(
        self,
        feature_profile: dict[str, Any],
        realized_return: float,
        outcome: str,
    ) -> dict[str, float]:
        changed: dict[str, float] = {}
        magnitude = min(abs(realized_return) / 0.05, 2.0)

        for key in FEATURE_KEYS:
            raw_value = feature_profile.get(key)
            value = float(raw_value) if isinstance(raw_value, (int, float)) else 0.0
            exposure = max(0.0, value)
            if exposure == 0:
                continue

            before = self.feature_penalties.get(key, 0.0)
            after = before

            if outcome == "bad_call":
                after = _clamp(
                    before + (self.learning_rate * magnitude * exposure),
                    0.0,
                    self.max_feature_penalty,
                )
            elif outcome == "good_call":
                after = _clamp(
                    before - (0.5 * self.learning_rate * magnitude * exposure),
                    0.0,
                    self.max_feature_penalty,
                )

            if after != before:
                self.feature_penalties[key] = after
                changed[key] = after - before

        return changed
