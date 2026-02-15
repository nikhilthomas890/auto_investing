from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from ..core.config import BotConfig
from ..data.news import NewsItem


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        return {}

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last <= first:
        return {}

    fragment = text[first : last + 1]
    try:
        data = json.loads(fragment)
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}


def _openai_json_response(
    *,
    api_key: str,
    model: str,
    timeout_seconds: float,
    system_content: str,
    user_content: str,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> dict[str, Any] | None:
    body: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
    }
    if isinstance(max_tokens, int) and max_tokens > 0:
        body["max_tokens"] = max_tokens

    request = Request(
        url="https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))

    raw_content = ""
    try:
        raw_content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    except Exception:
        raw_content = ""

    return _extract_json(raw_content if isinstance(raw_content, str) else "")


@dataclass(frozen=True)
class AIOutlook:
    short_term: float
    long_term: float
    confidence: float
    summary: str


@dataclass(frozen=True)
class LLMDecisionPlan:
    equity_buy_symbols: list[str]
    option_buy_symbols: list[str]
    exit_symbols: list[str]
    confidence: float
    summary: str
    rationale_by_symbol: dict[str, str]
    raw: dict[str, Any]


def _normalize_symbol_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    symbols: list[str] = []
    seen: set[str] = set()
    for item in value:
        symbol = str(item or "").strip().upper()
        if not symbol:
            continue
        if len(symbol) > 12:
            continue
        if any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for ch in symbol):
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
        if len(symbols) >= limit:
            break
    return symbols


class LongTermMemoryStore:
    def __init__(self, path: str, alpha: float) -> None:
        self.path = Path(path)
        self.alpha = _clamp(alpha, 0.0, 1.0)
        self.state: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(payload, dict):
            return

        normalized: dict[str, dict[str, Any]] = {}
        for symbol, raw in payload.items():
            if not isinstance(symbol, str) or not isinstance(raw, dict):
                continue
            score = raw.get("score")
            if not isinstance(score, (int, float)):
                continue
            row: dict[str, Any] = {
                "score": float(score),
                "updated_at": raw.get("updated_at"),
            }
            last_prediction = raw.get("last_prediction")
            if isinstance(last_prediction, (int, float)):
                row["last_prediction"] = float(last_prediction)
            last_price = raw.get("last_price")
            if isinstance(last_price, (int, float)) and float(last_price) > 0:
                row["last_price"] = float(last_price)
            normalized[symbol.upper()] = row
        self.state = normalized

    def _save(self) -> None:
        try:
            if self.path.parent != Path("."):
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        except Exception as exc:
            logging.warning("Failed writing long-term AI state %s: %s", self.path, exc)

    def get(self, symbol: str) -> float:
        entry = self.state.get(symbol.upper())
        if not entry:
            return 0.0
        score = entry.get("score")
        return float(score) if isinstance(score, (int, float)) else 0.0

    def update(self, symbol: str, current_score: float) -> float:
        key = symbol.upper()
        clamped = _clamp(current_score, -1.0, 1.0)
        previous = self.get(key)

        if key in self.state:
            blended = (self.alpha * clamped) + ((1.0 - self.alpha) * previous)
        else:
            blended = clamped

        blended = _clamp(blended, -1.0, 1.0)
        row = dict(self.state.get(key) or {})
        row["score"] = blended
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.state[key] = row
        self._save()
        return blended

    def record_prediction(self, symbol: str, prediction_score: float, reference_price: float) -> None:
        if reference_price <= 0:
            return
        key = symbol.upper()
        row = dict(self.state.get(key) or {"score": self.get(key)})
        row["last_prediction"] = _clamp(prediction_score, -1.0, 1.0)
        row["last_price"] = float(reference_price)
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.state[key] = row
        self._save()

    def apply_price_feedback(self, symbol: str, current_price: float, strength: float) -> float:
        if current_price <= 0:
            return 0.0

        key = symbol.upper()
        row = self.state.get(key)
        if not row:
            return 0.0

        prediction = row.get("last_prediction")
        reference_price = row.get("last_price")
        if not isinstance(prediction, (int, float)):
            return 0.0
        if not isinstance(reference_price, (int, float)) or float(reference_price) <= 0:
            return 0.0

        pred = _clamp(float(prediction), -1.0, 1.0)
        ref_price = float(reference_price)
        realized_return = (current_price / ref_price) - 1.0

        # Normalize realized move into [-1, 1], mapping +/-12.5% to full score.
        realized_signal = _clamp(realized_return / 0.125, -1.0, 1.0)
        agreement = pred * realized_signal

        # Wrong-way moves (negative agreement) reduce stored long-term conviction.
        feedback_strength = _clamp(strength, 0.0, 1.0)
        adjustment = feedback_strength * agreement * abs(pred)

        updated_score = _clamp(self.get(key) + adjustment, -1.0, 1.0)
        row["score"] = updated_score
        row["last_price"] = float(current_price)
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.state[key] = row
        self._save()
        return adjustment


class OpenAINewsInterpreter:
    def __init__(self, config: BotConfig) -> None:
        self.enabled = bool(config.enable_ai_news_interpreter)
        self.api_key = config.ai_api_key
        self.model = config.ai_model_name
        self.timeout_seconds = config.ai_timeout_seconds

        if config.ai_provider != "openai":
            self.enabled = False

        if self.enabled and not self.api_key:
            logging.warning(
                "AI interpreter enabled but OPENAI_API_KEY is missing. Falling back to non-AI scoring."
            )
            self.enabled = False

    def analyze(self, symbol: str, query: str, news_items: list[NewsItem]) -> AIOutlook:
        if not self.enabled or not news_items:
            return AIOutlook(short_term=0.0, long_term=0.0, confidence=0.0, summary="")

        lines: list[str] = []
        for item in news_items[:12]:
            source_parts = [part for part in (item.source_type, item.source, item.author) if part]
            source = f"[{' | '.join(source_parts)}] " if source_parts else ""
            context = (item.content or item.description or "").strip()
            if context:
                context = " ".join(context.split())
                if len(context) > 450:
                    context = context[:447].rstrip() + "..."
                lines.append(f"- {source}{item.title} | {context}")
            else:
                lines.append(f"- {source}{item.title}")

        user_content = (
            f"Symbol: {symbol}\n"
            f"Theme query: {query}\n"
            "Recent coverage:\n"
            f"{chr(10).join(lines)}\n\n"
            "Evaluate outlook from this news only.\n"
            "Return JSON with keys:\n"
            "short_term (float -1 to 1, 1-10 day view),\n"
            "long_term (float -1 to 1, 3-12 month view),\n"
            "confidence (float 0 to 1),\n"
            "summary (max 30 words)."
        )

        try:
            parsed = _openai_json_response(
                api_key=self.api_key,
                model=self.model,
                timeout_seconds=self.timeout_seconds,
                system_content=(
                    "You are a cautious equity analyst. Avoid hype. "
                    "If evidence is mixed, output scores near 0."
                ),
                user_content=user_content,
                temperature=0.0,
            )
        except Exception as exc:
            logging.warning("OpenAI interpretation failed for %s: %s", symbol, exc)
            return AIOutlook(short_term=0.0, long_term=0.0, confidence=0.0, summary="")

        if not isinstance(parsed, dict):
            return AIOutlook(short_term=0.0, long_term=0.0, confidence=0.0, summary="")
        short_term = _to_float(parsed.get("short_term"), 0.0)
        long_term = _to_float(parsed.get("long_term"), 0.0)
        confidence = _to_float(parsed.get("confidence"), 0.0)
        summary = str(parsed.get("summary") or "").strip()

        return AIOutlook(
            short_term=_clamp(short_term, -1.0, 1.0),
            long_term=_clamp(long_term, -1.0, 1.0),
            confidence=_clamp(confidence, 0.0, 1.0),
            summary=summary,
        )


class OpenAIDecisionPlanner:
    def __init__(self, config: BotConfig) -> None:
        self.enabled = bool(config.enable_llm_first_decisioning)
        self.api_key = config.ai_api_key
        self.model = config.ai_model_name
        self.timeout_seconds = config.ai_timeout_seconds
        self.max_symbols = max(1, int(config.llm_first_max_symbols))

        if config.ai_provider != "openai":
            self.enabled = False

        if self.enabled and not self.api_key:
            logging.warning(
                "LLM-first decisioning enabled but OPENAI_API_KEY is missing. Falling back to rules-first decisions."
            )
            self.enabled = False

    def build_plan(
        self,
        *,
        symbol_contexts: list[dict[str, Any]],
        held_equities: list[str],
        held_option_underlyings: list[str],
    ) -> LLMDecisionPlan | None:
        if not self.enabled:
            return None
        if not symbol_contexts:
            return None

        context_rows: list[dict[str, Any]] = []
        for row in symbol_contexts[: self.max_symbols]:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            context_rows.append(
                {
                    "symbol": symbol,
                    "score": _to_float(row.get("score"), 0.0),
                    "momentum_20d": _to_float(row.get("momentum_20d"), 0.0),
                    "momentum_5d": _to_float(row.get("momentum_5d"), 0.0),
                    "trend_20d": _to_float(row.get("trend_20d"), 0.0),
                    "volatility_20d": _to_float(row.get("volatility_20d"), 0.0),
                    "news_score": _to_float(row.get("news_score"), 0.0),
                    "macro_score": _to_float(row.get("macro_score"), 0.0),
                    "recent_research": row.get("recent_research") if isinstance(row.get("recent_research"), list) else [],
                }
            )

        if not context_rows:
            return None

        user_content = (
            "You are selecting candidate trades for an AI-themed portfolio.\n"
            "Prioritize high-conviction, risk-aware ideas only.\n"
            "Use the provided signal metrics and research snippets.\n"
            "Return JSON with keys:\n"
            "equity_buy_symbols (list of ticker strings),\n"
            "option_buy_symbols (list of ticker strings),\n"
            "exit_symbols (list of ticker strings),\n"
            "confidence (0 to 1),\n"
            "summary (max 50 words),\n"
            "rationale_by_symbol (object symbol -> short reason).\n\n"
            f"Held equities: {', '.join(sorted({item.upper() for item in held_equities if item.strip()})) or 'none'}\n"
            "Held option underlyings: "
            f"{', '.join(sorted({item.upper() for item in held_option_underlyings if item.strip()})) or 'none'}\n\n"
            "Symbol context JSON:\n"
            f"{json.dumps(context_rows, ensure_ascii=True)}"
        )

        try:
            parsed = _openai_json_response(
                api_key=self.api_key,
                model=self.model,
                timeout_seconds=self.timeout_seconds,
                system_content=(
                    "You are a disciplined portfolio manager. Do not force trades. "
                    "Output only symbols from the provided context."
                ),
                user_content=user_content,
                temperature=0.0,
                max_tokens=700,
            )
        except Exception as exc:
            logging.warning("OpenAI LLM-first planning failed: %s", exc)
            return None

        if not isinstance(parsed, dict):
            return None

        equity_buy_symbols = _normalize_symbol_list(
            parsed.get("equity_buy_symbols", parsed.get("equity_entries", [])),
            limit=self.max_symbols,
        )
        option_buy_symbols = _normalize_symbol_list(
            parsed.get("option_buy_symbols", parsed.get("option_entries", [])),
            limit=self.max_symbols,
        )
        exit_symbols = _normalize_symbol_list(
            parsed.get("exit_symbols", parsed.get("reductions", [])),
            limit=self.max_symbols,
        )
        confidence = _clamp(_to_float(parsed.get("confidence"), 0.0), 0.0, 1.0)
        summary = str(parsed.get("summary") or "").strip()
        rationale_raw = parsed.get("rationale_by_symbol")
        rationale_by_symbol: dict[str, str] = {}
        if isinstance(rationale_raw, dict):
            for key, value in rationale_raw.items():
                symbol = str(key or "").strip().upper()
                reason = str(value or "").strip()
                if symbol and reason:
                    rationale_by_symbol[symbol] = reason[:320]

        return LLMDecisionPlan(
            equity_buy_symbols=equity_buy_symbols,
            option_buy_symbols=option_buy_symbols,
            exit_symbols=exit_symbols,
            confidence=confidence,
            summary=summary,
            rationale_by_symbol=rationale_by_symbol,
            raw=parsed,
        )


def _to_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
