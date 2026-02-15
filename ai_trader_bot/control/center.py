from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import BotConfig

_BLOCKED_KEYS = {
    "restrict_fund_transfers",
    "ai_api_key",
    "fmp_api_key",
    "finnhub_api_key",
    "schwab_api_key",
    "schwab_app_secret",
    "schwab_callback_url",
    "schwab_token_path",
    "schwab_account_number",
}

_RESTART_RECOMMENDED_KEYS = {
    "ai_provider",
    "ai_model_name",
    "ai_api_key",
    "ai_timeout_seconds",
    "ai_long_term_memory_alpha",
    "ai_long_term_state_path",
    "historical_research_state_path",
    "historical_research_memory_alpha",
    "decision_learning_state_path",
    "decision_journal_path",
    "macro_long_term_state_path",
    "macro_long_term_memory_alpha",
    "report_timezone",
    "activity_log_path",
    "portfolio_log_path",
    "metadata_log_path",
    "daily_report_log_path",
    "weekly_report_log_path",
    "research_log_path",
    "quarterly_model_advisor_log_path",
    "model_roadmap_log_path",
    "bootstrap_optimization_log_path",
    "report_state_path",
    "system_log_path",
    "dashboard_host",
    "dashboard_port",
    "enable_dashboard",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    except Exception:
        return []
    return rows


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    try:
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception as exc:
        logging.warning("Failed writing control log %s: %s", path, exc)


class DecisionControlCenter:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.actions_path = Path(config.control_actions_log_path)
        self.results_path = Path(config.control_results_log_path)
        self.overrides_path = Path(config.runtime_overrides_path)
        self.model_requests_path = Path(config.model_build_requests_path)
        self._lock = threading.RLock()

        self._field_types = self._build_field_type_index()
        self._processed_ids = self._load_processed_ids()
        self._runtime_overrides = self._load_overrides()
        self.apply_saved_overrides()

    @staticmethod
    def _build_field_type_index() -> dict[str, Any]:
        index: dict[str, Any] = {}
        if is_dataclass(BotConfig):
            for item in fields(BotConfig):
                index[item.name] = item.type
        return index

    def _load_processed_ids(self) -> set[str]:
        rows = _read_jsonl(self.results_path)
        ids: set[str] = set()
        for row in rows:
            action_id = str(row.get("action_id") or "").strip()
            if action_id:
                ids.add(action_id)
        return ids

    def _load_overrides(self) -> dict[str, Any]:
        if not self.overrides_path.exists():
            return {}
        try:
            payload = json.loads(self.overrides_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return dict(payload)

    def _save_overrides(self) -> None:
        try:
            if self.overrides_path.parent != Path("."):
                self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
            self.overrides_path.write_text(json.dumps(self._runtime_overrides, indent=2), encoding="utf-8")
        except Exception as exc:
            logging.warning("Failed writing runtime overrides %s: %s", self.overrides_path, exc)

    def apply_saved_overrides(self) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        with self._lock:
            for key, raw_value in self._runtime_overrides.items():
                status = self._apply_config_value(key, raw_value, persist=False)
                if status["status"] == "applied":
                    applied.append(status)
        return applied

    def get_overrides(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._runtime_overrides)

    def list_configurable_keys(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self._lock:
            for key in sorted(self._field_types.keys()):
                if key in _BLOCKED_KEYS:
                    continue
                if not hasattr(self.config, key):
                    continue

                current = getattr(self.config, key)
                if isinstance(current, bool):
                    value_type = "bool"
                elif isinstance(current, int) and not isinstance(current, bool):
                    value_type = "int"
                elif isinstance(current, float):
                    value_type = "float"
                elif isinstance(current, list):
                    value_type = "list"
                elif isinstance(current, str):
                    value_type = "str"
                else:
                    continue

                rows.append(
                    {
                        "key": key,
                        "value_type": value_type,
                        "current_value": current,
                        "restart_recommended": key in _RESTART_RECOMMENDED_KEYS,
                    }
                )
        return rows

    def submit_action(
        self,
        *,
        action_type: str,
        payload: dict[str, Any],
        requested_by: str = "dashboard",
    ) -> dict[str, Any]:
        normalized_type = (action_type or "").strip().lower()
        with self._lock:
            action = {
                "event": "control_action_submitted",
                "action_id": str(uuid.uuid4()),
                "timestamp": _utc_now_iso(),
                "action_type": normalized_type,
                "requested_by": (requested_by or "dashboard").strip() or "dashboard",
                "payload": payload if isinstance(payload, dict) else {},
            }
            _append_jsonl(self.actions_path, action)
        return action

    def list_actions(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = _read_jsonl(self.actions_path)
            if limit > 0 and len(rows) > limit:
                rows = rows[-limit:]
            return rows

    def list_results(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = _read_jsonl(self.results_path)
            if limit > 0 and len(rows) > limit:
                rows = rows[-limit:]
            return rows

    def process_pending_actions(self, *, max_actions: int | None = None) -> dict[str, Any]:
        with self._lock:
            queued = self.list_actions(limit=20000)
            limit = max_actions if isinstance(max_actions, int) and max_actions > 0 else self.config.control_max_actions_per_cycle
            limit = max(1, int(limit))

            processed = 0
            outcomes: list[dict[str, Any]] = []
            restart_recommended = False
            deploy_recommended = False

            for action in queued:
                action_id = str(action.get("action_id") or "").strip()
                if not action_id or action_id in self._processed_ids:
                    continue

                result = self._process_one(action)
                _append_jsonl(self.results_path, result)
                self._processed_ids.add(action_id)
                outcomes.append(result)
                processed += 1

                restart_recommended = restart_recommended or bool(result.get("restart_recommended", False))
                deploy_recommended = deploy_recommended or bool(result.get("deploy_recommended", False))

                if processed >= limit:
                    break

        return {
            "processed": processed,
            "restart_recommended": restart_recommended,
            "deploy_recommended": deploy_recommended,
            "outcomes": outcomes,
        }

    def _process_one(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = str(action.get("action_type") or "").strip().lower()
        action_id = str(action.get("action_id") or "").strip()
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}

        base = {
            "event": "control_action_result",
            "timestamp": _utc_now_iso(),
            "action_id": action_id,
            "action_type": action_type,
            "status": "rejected",
            "message": "Unsupported action",
            "restart_recommended": False,
            "deploy_recommended": False,
            "changes": [],
        }

        if action_type == "set_config":
            key = str(payload.get("key") or "").strip()
            value = payload.get("value")
            result = self._apply_config_value(key, value, persist=True)
            base.update(result)
            return base

        if action_type == "restart_runtime":
            base.update(
                {
                    "status": "applied",
                    "message": "Runtime restart requested.",
                    "restart_recommended": True,
                }
            )
            return base

        if action_type == "redeploy_code":
            base.update(
                {
                    "status": "queued",
                    "message": "Code redeploy request queued for deployment pipeline.",
                    "deploy_recommended": True,
                }
            )
            return base

        if action_type == "new_model_request":
            model_name = str(payload.get("model_name") or "").strip()
            rationale = str(payload.get("rationale") or "").strip()
            target = str(payload.get("target_quarter") or "").strip()
            request = {
                "event": "model_build_request",
                "timestamp": _utc_now_iso(),
                "action_id": action_id,
                "model_name": model_name,
                "rationale": rationale,
                "target_quarter": target,
                "status": "queued_for_build",
            }
            _append_jsonl(self.model_requests_path, request)
            base.update(
                {
                    "status": "queued",
                    "message": "Model build request queued for implementation.",
                    "deploy_recommended": True,
                    "changes": [request],
                }
            )
            return base

        return base

    def _coerce_value(self, key: str, raw_value: Any) -> tuple[bool, Any, str]:
        if not hasattr(self.config, key):
            return False, None, f"Unknown config key: {key}"

        current = getattr(self.config, key)

        if isinstance(current, bool):
            if isinstance(raw_value, bool):
                return True, raw_value, ""
            if isinstance(raw_value, str):
                lowered = raw_value.strip().lower()
                if lowered in {"1", "true", "yes", "y", "on"}:
                    return True, True, ""
                if lowered in {"0", "false", "no", "n", "off"}:
                    return True, False, ""
            return False, None, f"Invalid bool value for {key}"

        if isinstance(current, int) and not isinstance(current, bool):
            try:
                return True, int(raw_value), ""
            except (TypeError, ValueError):
                return False, None, f"Invalid int value for {key}"

        if isinstance(current, float):
            try:
                return True, float(raw_value), ""
            except (TypeError, ValueError):
                return False, None, f"Invalid float value for {key}"

        if isinstance(current, list):
            if isinstance(raw_value, list):
                values = [str(item).strip() for item in raw_value if str(item).strip()]
                return True, values, ""
            if isinstance(raw_value, str):
                values = [chunk.strip() for chunk in raw_value.split(",") if chunk.strip()]
                return True, values, ""
            return False, None, f"Invalid list value for {key}"

        if isinstance(current, str):
            return True, str(raw_value), ""

        return False, None, f"Unsupported config type for {key}"

    def _apply_config_value(self, key: str, raw_value: Any, *, persist: bool) -> dict[str, Any]:
        response = {
            "status": "rejected",
            "message": "",
            "restart_recommended": False,
            "deploy_recommended": False,
            "changes": [],
        }

        if not key:
            response["message"] = "Missing config key"
            return response

        if key in _BLOCKED_KEYS:
            response["message"] = f"Config key is protected and cannot be changed via dashboard: {key}"
            return response

        ok, value, error = self._coerce_value(key, raw_value)
        if not ok:
            response["message"] = error
            return response

        before = getattr(self.config, key)
        if before == value:
            response.update(
                {
                    "status": "applied",
                    "message": f"No change for {key}; value already current.",
                    "changes": [{"key": key, "old": before, "new": value}],
                }
            )
            return response

        setattr(self.config, key, value)
        if persist:
            self._runtime_overrides[key] = value
            self._save_overrides()

        restart_needed = key in _RESTART_RECOMMENDED_KEYS
        response.update(
            {
                "status": "applied",
                "message": f"Updated {key} from {before!r} to {value!r}.",
                "restart_recommended": restart_needed,
                "changes": [{"key": key, "old": before, "new": value}],
            }
        )
        return response
