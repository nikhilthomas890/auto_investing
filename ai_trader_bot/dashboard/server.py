from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from ..control import DecisionControlCenter
from ..core.config import BotConfig


class DashboardHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        config: BotConfig,
        control_center: DecisionControlCenter | None = None,
    ) -> None:
        super().__init__(server_address, DashboardRequestHandler)
        self.config = config
        self.control_center = control_center
        self.static_dir = Path(__file__).with_name("static")
        self.todo_path = Path(__file__).with_name("todo_items.json")
        self.todo_lock = threading.RLock()
        self.report_tz = _resolve_timezone(config.report_timezone)


def _resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _parse_iso(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _tail_lines(path: Path, limit: int) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    if limit > 0 and len(lines) > limit:
        return lines[-limit:]
    return lines


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        params = parse_qs(parsed.query)

        if route == "/":
            self._redirect("/portfolio")
            return

        if route in {"/portfolio", "/research", "/reports", "/logs", "/control", "/todo"}:
            self._serve_static_file(f"{route[1:]}.html")
            return

        if route.startswith("/static/"):
            self._serve_static_file(route.replace("/static/", "", 1))
            return

        if route == "/api/portfolio/latest":
            self._json(self._portfolio_payload())
            return

        if route == "/api/research":
            self._json(self._research_payload(params))
            return

        if route == "/api/reports":
            self._json(self._reports_payload(params))
            return

        if route == "/api/system-logs":
            self._json(self._system_logs_payload(params))
            return

        if route == "/api/todo":
            self._json(self._todo_payload())
            return

        if route == "/api/control/actions":
            self._json(self._control_actions_payload(params))
            return

        if route == "/api/control/results":
            self._json(self._control_results_payload(params))
            return

        if route == "/api/control/overrides":
            self._json(self._control_overrides_payload())
            return

        if route == "/api/control/configurable":
            self._json(self._control_configurable_payload())
            return

        if route == "/api/health":
            self._json({"ok": True, "timestamp": datetime.now(timezone.utc).isoformat()})
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/api/control/actions":
            self._post_control_action()
            return

        if route == "/api/todo/items":
            self._post_todo_item()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        logging.info("dashboard %s - %s", self.address_string(), format % args)

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, message: str, *, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status=status)

    def _read_json_body(self, *, max_bytes: int = 200_000) -> dict[str, Any] | None:
        raw_length = self.headers.get("Content-Length", "0").strip()
        try:
            length = int(raw_length)
        except ValueError:
            return None
        if length <= 0 or length > max_bytes:
            return None
        try:
            body = self.rfile.read(length)
        except Exception:
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _redirect(self, target: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", target)
        self.end_headers()

    def _serve_static_file(self, name: str) -> None:
        path = (self.server.static_dir / name).resolve()
        if not path.exists() or not path.is_file() or self.server.static_dir.resolve() not in path.parents:
            self.send_error(HTTPStatus.NOT_FOUND, "Asset not found")
            return

        ctype, _ = mimetypes.guess_type(str(path))
        if not ctype:
            ctype = "text/plain; charset=utf-8"

        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _portfolio_payload(self) -> dict[str, Any]:
        config = self.server.config
        snapshots = _read_jsonl(Path(config.portfolio_log_path))
        latest = snapshots[-1] if snapshots else {}

        equity_positions = latest.get("equity_positions") if isinstance(latest.get("equity_positions"), dict) else {}
        option_positions = latest.get("option_positions") if isinstance(latest.get("option_positions"), dict) else {}

        activity = _read_jsonl(Path(config.activity_log_path))
        recent_trades = activity[-50:]

        return {
            "timestamp": str(latest.get("timestamp") or ""),
            "cash": float(latest.get("cash", 0.0) or 0.0),
            "account_equity": float(latest.get("account_equity", 0.0) or 0.0),
            "equity_positions": [
                {"symbol": str(symbol), "quantity": int(quantity)}
                for symbol, quantity in sorted(equity_positions.items(), key=lambda row: row[0])
            ],
            "open_calls": [
                {"symbol": str(symbol), "quantity": int(quantity)}
                for symbol, quantity in sorted(option_positions.items(), key=lambda row: row[0])
                if int(quantity) > 0
            ],
            "recent_trades": recent_trades,
        }

    def _research_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        config = self.server.config
        selected_date = ""
        if params.get("date"):
            selected_date = str(params["date"][0]).strip()
        if not selected_date:
            selected_date = datetime.now(timezone.utc).astimezone(self.server.report_tz).date().isoformat()

        try:
            selected_day = date.fromisoformat(selected_date)
        except ValueError:
            selected_day = datetime.now(timezone.utc).astimezone(self.server.report_tz).date()
            selected_date = selected_day.isoformat()

        rows = _read_jsonl(Path(config.research_log_path))
        filtered: list[dict[str, Any]] = []
        for row in rows:
            ts = _parse_iso(str(row.get("timestamp") or ""))
            if ts is None:
                continue
            if ts.astimezone(self.server.report_tz).date() != selected_day:
                continue
            filtered.append(row)

        filtered.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)
        return {
            "date": selected_date,
            "count": len(filtered),
            "items": filtered[:1200],
        }

    def _reports_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        config = self.server.config
        report_type = str((params.get("type") or ["all"])[0]).strip().lower() or "all"
        limit = int((params.get("limit") or ["200"])[0])
        limit = max(10, min(limit, 2000))

        mapping = {
            "daily": Path(config.daily_report_log_path),
            "weekly": Path(config.weekly_report_log_path),
            "quarterly": Path(config.quarterly_model_advisor_log_path),
            "roadmap": Path(config.model_roadmap_log_path),
            "bootstrap": Path(config.bootstrap_optimization_log_path),
            "layers": Path(config.layer_reevaluation_log_path),
        }

        def rows_for(path: Path) -> list[dict[str, Any]]:
            rows = _read_jsonl(path)
            if len(rows) > limit:
                return rows[-limit:]
            return rows

        if report_type == "all":
            return {
                "type": "all",
                "reports": {key: rows_for(path) for key, path in mapping.items()},
            }

        path = mapping.get(report_type)
        if path is None:
            return {
                "type": report_type,
                "reports": [],
                "error": "Unsupported report type",
            }
        return {
            "type": report_type,
            "reports": rows_for(path),
        }

    def _system_logs_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        config = self.server.config
        limit = int((params.get("limit") or ["400"])[0])
        limit = max(50, min(limit, 5000))
        lines = _tail_lines(Path(config.system_log_path), limit)
        return {
            "path": config.system_log_path,
            "count": len(lines),
            "lines": lines,
        }

    def _todo_payload(self) -> dict[str, Any]:
        with self.server.todo_lock:
            payload = self._read_todo_document()
        items = payload.get("items")
        item_rows = items if isinstance(items, list) else []
        response = dict(payload)
        response["count"] = len(item_rows)
        response["items"] = [row for row in item_rows if isinstance(row, dict)]
        return response

    @staticmethod
    def _sanitize_todo_text(value: Any, *, max_chars: int = 400) -> str:
        text = str(value or "").strip()
        if max_chars <= 0:
            return text
        return text[:max_chars]

    @staticmethod
    def _sanitize_todo_details(value: Any) -> list[str]:
        rows: list[str] = []
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text:
                    rows.append(text[:300])
            return rows[:24]

        if isinstance(value, str):
            for line in value.splitlines():
                text = line.strip()
                if text:
                    rows.append(text[:300])
            return rows[:24]

        return []

    @staticmethod
    def _normalize_priority(raw: Any) -> str:
        value = str(raw or "P2").strip().upper()
        if value not in {"P0", "P1", "P2", "P3"}:
            return "P2"
        return value

    @staticmethod
    def _normalize_status(raw: Any) -> str:
        value = str(raw or "planned").strip().lower()
        if value not in {"planned", "in_progress", "blocked", "completed", "deferred"}:
            return "planned"
        return value

    @staticmethod
    def _slugify_todo_id(raw: str) -> str:
        token = re.sub(r"[^a-z0-9]+", "-", str(raw or "").strip().lower()).strip("-")
        return token[:64] if token else ""

    def _unique_todo_id(self, desired: str, used: set[str]) -> str:
        base = self._slugify_todo_id(desired) or f"todo-{int(datetime.now(timezone.utc).timestamp())}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _normalize_todo_item(self, raw_item: dict[str, Any], *, require_title: bool) -> tuple[dict[str, Any], str]:
        title = self._sanitize_todo_text(raw_item.get("title"), max_chars=160)
        if require_title and not title:
            return {}, "Missing item title."

        item = {
            "id": self._slugify_todo_id(str(raw_item.get("id") or "")),
            "title": title,
            "priority": self._normalize_priority(raw_item.get("priority")),
            "status": self._normalize_status(raw_item.get("status")),
            "category": self._sanitize_todo_text(raw_item.get("category"), max_chars=80) or "general",
            "target_window": self._sanitize_todo_text(raw_item.get("target_window"), max_chars=80) or "backlog",
            "estimate_codex": self._sanitize_todo_text(raw_item.get("estimate_codex"), max_chars=60),
            "summary": self._sanitize_todo_text(raw_item.get("summary"), max_chars=500),
            "details": self._sanitize_todo_details(raw_item.get("details")),
        }
        return item, ""

    def _read_todo_document(self) -> dict[str, Any]:
        path = self.server.todo_path
        if not path.exists():
            return {
                "title": "Implementation To-Do",
                "updated_at": "",
                "items": [],
            }

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "title": "Implementation To-Do",
                "updated_at": "",
                "items": [],
            }

        if not isinstance(payload, dict):
            return {
                "title": "Implementation To-Do",
                "updated_at": "",
                "items": [],
            }

        normalized = dict(payload)
        normalized["title"] = str(payload.get("title") or "Implementation To-Do")
        normalized["updated_at"] = str(payload.get("updated_at") or "")
        items_raw = payload.get("items")
        normalized["items"] = [row for row in items_raw if isinstance(row, dict)] if isinstance(items_raw, list) else []
        return normalized

    def _write_todo_document(self, payload: dict[str, Any]) -> None:
        path = self.server.todo_path
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = datetime.now(timezone.utc).date().isoformat()
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _post_todo_item(self) -> None:
        request = self._read_json_body(max_bytes=300_000)
        if request is None:
            self._json_error("Invalid JSON payload.")
            return

        action = str(request.get("action") or "create").strip().lower() or "create"
        if action not in {"create", "update", "delete"}:
            self._json_error("Unsupported todo action. Use create, update, or delete.")
            return

        try:
            with self.server.todo_lock:
                document = self._read_todo_document()
                items = [row for row in document.get("items", []) if isinstance(row, dict)]
                used_ids = {str(row.get("id") or "").strip() for row in items if str(row.get("id") or "").strip()}

                if action == "create":
                    raw_item = request.get("item")
                    if not isinstance(raw_item, dict):
                        self._json_error("Missing item object for create action.")
                        return
                    item, error = self._normalize_todo_item(raw_item, require_title=True)
                    if error:
                        self._json_error(error)
                        return
                    item["id"] = self._unique_todo_id(item.get("id") or item["title"], used_ids)
                    items.append(item)
                    document["items"] = items
                    self._write_todo_document(document)
                    self._json({"ok": True, "action": "create", "item": item, "count": len(items)}, status=201)
                    return

                item_id = self._slugify_todo_id(str(request.get("id") or request.get("item_id") or ""))
                if not item_id:
                    self._json_error("Missing to-do id.")
                    return

                item_index = -1
                for idx, item in enumerate(items):
                    if self._slugify_todo_id(str(item.get("id") or "")) == item_id:
                        item_index = idx
                        break
                if item_index < 0:
                    self._json_error("To-do item not found.", status=404)
                    return

                if action == "delete":
                    removed = items.pop(item_index)
                    document["items"] = items
                    self._write_todo_document(document)
                    self._json({"ok": True, "action": "delete", "item": removed, "count": len(items)})
                    return

                patch = request.get("item")
                if not isinstance(patch, dict):
                    self._json_error("Missing item object for update action.")
                    return
                merged = dict(items[item_index])
                merged.update(patch)
                normalized_item, error = self._normalize_todo_item(merged, require_title=True)
                if error:
                    self._json_error(error)
                    return
                normalized_item["id"] = items[item_index].get("id") or item_id
                items[item_index] = normalized_item
                document["items"] = items
                self._write_todo_document(document)
                self._json({"ok": True, "action": "update", "item": normalized_item, "count": len(items)})
        except Exception as exc:
            logging.exception("To-do update failed: %s", exc)
            self._json_error("Failed to update to-do items.", status=500)
            return

    def _control_actions_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        config = self.server.config
        if not config.enable_dashboard_control:
            return {"ok": False, "error": "Dashboard control is disabled."}
        if self.server.control_center is None:
            return {"ok": False, "error": "Control center is unavailable in this runtime."}

        limit = int((params.get("limit") or ["200"])[0])
        limit = max(10, min(limit, 2000))
        actions = self.server.control_center.list_actions(limit=limit)
        return {"ok": True, "count": len(actions), "actions": actions}

    def _control_results_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        config = self.server.config
        if not config.enable_dashboard_control:
            return {"ok": False, "error": "Dashboard control is disabled."}
        if self.server.control_center is None:
            return {"ok": False, "error": "Control center is unavailable in this runtime."}

        limit = int((params.get("limit") or ["200"])[0])
        limit = max(10, min(limit, 2000))
        rows = self.server.control_center.list_results(limit=limit)
        return {"ok": True, "count": len(rows), "results": rows}

    def _control_overrides_payload(self) -> dict[str, Any]:
        config = self.server.config
        if not config.enable_dashboard_control:
            return {"ok": False, "error": "Dashboard control is disabled."}
        if self.server.control_center is None:
            return {"ok": False, "error": "Control center is unavailable in this runtime."}
        overrides = self.server.control_center.get_overrides()
        return {"ok": True, "count": len(overrides), "overrides": overrides}

    def _control_configurable_payload(self) -> dict[str, Any]:
        config = self.server.config
        if not config.enable_dashboard_control:
            return {"ok": False, "error": "Dashboard control is disabled."}
        if self.server.control_center is None:
            return {"ok": False, "error": "Control center is unavailable in this runtime."}
        keys = self.server.control_center.list_configurable_keys()
        return {"ok": True, "count": len(keys), "keys": keys}

    def _post_control_action(self) -> None:
        config = self.server.config
        if not config.enable_dashboard_control:
            self._json_error("Dashboard control is disabled.", status=403)
            return

        control = self.server.control_center
        if control is None:
            self._json_error("Control center is unavailable in this runtime.", status=503)
            return

        request = self._read_json_body()
        if request is None:
            self._json_error("Invalid JSON payload.")
            return

        action_type = str(request.get("action_type") or "").strip().lower()
        if not action_type:
            self._json_error("Missing action_type.")
            return

        action_payload = request.get("payload")
        if action_payload is None:
            action_payload = {}
        if not isinstance(action_payload, dict):
            self._json_error("payload must be an object when provided.")
            return

        requested_by = str(request.get("requested_by") or "dashboard_ui").strip() or "dashboard_ui"
        apply_now = bool(request.get("apply_now", config.control_auto_apply_on_submit))

        action = control.submit_action(
            action_type=action_type,
            payload=action_payload,
            requested_by=requested_by,
        )

        process_result: dict[str, Any] | None = None
        action_outcome: dict[str, Any] | None = None
        if apply_now:
            process_result = control.process_pending_actions(max_actions=config.control_max_actions_per_cycle)
            outcomes = process_result.get("outcomes")
            if isinstance(outcomes, list):
                for row in outcomes:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("action_id") or "") == str(action.get("action_id") or ""):
                        action_outcome = row
                        break

        self._json(
            {
                "ok": True,
                "action": action,
                "apply_now": apply_now,
                "result": action_outcome,
                "processing": process_result,
            },
            status=201,
        )


def start_dashboard_server(
    config: BotConfig,
    control_center: DecisionControlCenter | None = None,
) -> DashboardHTTPServer:
    server = DashboardHTTPServer((config.dashboard_host, config.dashboard_port), config, control_center=control_center)
    thread = threading.Thread(target=server.serve_forever, name="dashboard-server", daemon=True)
    thread.start()
    return server


def run_dashboard() -> None:
    config = BotConfig.from_env(force_live=None, interval_override=None)
    control_center = DecisionControlCenter(config) if config.enable_dashboard_control else None
    server = DashboardHTTPServer((config.dashboard_host, config.dashboard_port), config, control_center=control_center)
    logging.info("Dashboard listening at http://%s:%d", config.dashboard_host, config.dashboard_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
