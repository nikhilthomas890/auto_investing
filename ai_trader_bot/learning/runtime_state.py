from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


@dataclass
class RuntimeState:
    last_research_pull_at: str = ""
    last_warmup_date_local: str = ""
    first_start_date_local: str = ""
    bootstrap_complete_date_local: str = ""


class RuntimeStateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.state = self._load()

    def _load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return RuntimeState()
        if not isinstance(payload, dict):
            return RuntimeState()
        return RuntimeState(
            last_research_pull_at=str(payload.get("last_research_pull_at") or ""),
            last_warmup_date_local=str(payload.get("last_warmup_date_local") or ""),
            first_start_date_local=str(payload.get("first_start_date_local") or ""),
            bootstrap_complete_date_local=str(payload.get("bootstrap_complete_date_local") or ""),
        )

    def _save(self) -> None:
        payload = {
            "last_research_pull_at": self.state.last_research_pull_at,
            "last_warmup_date_local": self.state.last_warmup_date_local,
            "first_start_date_local": self.state.first_start_date_local,
            "bootstrap_complete_date_local": self.state.bootstrap_complete_date_local,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            if self.path.parent != Path("."):
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logging.warning("Failed writing runtime state %s: %s", self.path, exc)

    @staticmethod
    def _parse_ts(raw: str) -> datetime | None:
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

    def get_last_research_pull_at(self) -> datetime | None:
        return self._parse_ts(self.state.last_research_pull_at)

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    def mark_research_pull(self, when_utc: datetime | None = None) -> None:
        now = (when_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.state.last_research_pull_at = now.isoformat()
        self._save()

    def is_warmup_done_for_day(self, local_day: date) -> bool:
        return self.state.last_warmup_date_local == local_day.isoformat()

    def mark_warmup_done_for_day(self, local_day: date) -> None:
        self.state.last_warmup_date_local = local_day.isoformat()
        self._save()

    def get_first_start_date_local(self) -> date | None:
        return self._parse_date(self.state.first_start_date_local)

    def ensure_first_start_date_local(self, local_day: date) -> date:
        existing = self.get_first_start_date_local()
        if existing is not None:
            return existing
        self.state.first_start_date_local = local_day.isoformat()
        self._save()
        return local_day

    def get_bootstrap_complete_date_local(self) -> date | None:
        return self._parse_date(self.state.bootstrap_complete_date_local)

    def is_bootstrap_complete(self) -> bool:
        return self.get_bootstrap_complete_date_local() is not None

    def mark_bootstrap_complete(self, local_day: date) -> None:
        self.state.bootstrap_complete_date_local = local_day.isoformat()
        self._save()
