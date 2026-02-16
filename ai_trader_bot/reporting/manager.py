from __future__ import annotations

import json
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..core.config import BotConfig
from ..data.market_calendar import is_us_equity_market_day
from ..strategy.options import option_underlying

WEEKDAY_INDEX = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}


@dataclass
class ReportState:
    last_daily_report_date: str = ""
    last_weekly_report_key: str = ""
    last_layer_reevaluation_target: str = ""
    last_quarterly_advisor_target: str = ""
    last_model_roadmap_target: str = ""
    last_bootstrap_optimization_date: str = ""


class ReportManager:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.activity_path = Path(config.activity_log_path)
        self.portfolio_path = Path(config.portfolio_log_path)
        self.metadata_path = Path(config.metadata_log_path)
        self.research_path = Path(config.research_log_path)
        self.daily_report_path = Path(config.daily_report_log_path)
        self.weekly_report_path = Path(config.weekly_report_log_path)
        self.state_path = Path(config.report_state_path)
        self.quarterly_advisor_path = Path(config.quarterly_model_advisor_log_path)
        self.model_roadmap_path = Path(config.model_roadmap_log_path)
        self.bootstrap_optimization_path = Path(config.bootstrap_optimization_log_path)
        self.layer_reevaluation_path = Path(config.layer_reevaluation_log_path)
        self.decision_journal_path = Path(config.decision_journal_path)
        self.report_tz = self._resolve_timezone(config.report_timezone)

        self.state = self._load_state()
        self._research_seen_ids = self._load_recent_research_ids(limit=20000)

    @staticmethod
    def _resolve_timezone(name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except Exception:
            logging.warning("Invalid REPORT_TIMEZONE=%s, falling back to UTC.", name)
            return ZoneInfo("UTC")

    def _load_state(self) -> ReportState:
        if not self.state_path.exists():
            return ReportState()

        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return ReportState()

        if not isinstance(payload, dict):
            return ReportState()

        return ReportState(
            last_daily_report_date=str(payload.get("last_daily_report_date") or ""),
            last_weekly_report_key=str(payload.get("last_weekly_report_key") or ""),
            last_layer_reevaluation_target=str(
                payload.get("last_layer_reevaluation_target")
                or payload.get("last_layer_reevaluation_week_key")
                or ""
            ),
            last_quarterly_advisor_target=str(payload.get("last_quarterly_advisor_target") or ""),
            last_model_roadmap_target=str(payload.get("last_model_roadmap_target") or ""),
            last_bootstrap_optimization_date=str(payload.get("last_bootstrap_optimization_date") or ""),
        )

    def _save_state(self) -> None:
        payload = {
            "last_daily_report_date": self.state.last_daily_report_date,
            "last_weekly_report_key": self.state.last_weekly_report_key,
            "last_layer_reevaluation_target": self.state.last_layer_reevaluation_target,
            "last_quarterly_advisor_target": self.state.last_quarterly_advisor_target,
            "last_model_roadmap_target": self.state.last_model_roadmap_target,
            "last_bootstrap_optimization_date": self.state.last_bootstrap_optimization_date,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            if self.state_path.parent != Path("."):
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logging.warning("Failed to persist report state %s: %s", self.state_path, exc)

    @staticmethod
    def _parse_ts(raw: str) -> datetime | None:
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            if path.parent != Path("."):
                path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        except Exception as exc:
            logging.warning("Failed writing report log %s: %s", path, exc)

    def _load_recent_research_ids(self, *, limit: int) -> set[str]:
        if not self.research_path.exists():
            return set()

        try:
            lines = self.research_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return set()

        if limit > 0 and len(lines) > limit:
            lines = lines[-limit:]

        seen: set[str] = set()
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            item_id = str(payload.get("item_id") or "").strip()
            if item_id:
                seen.add(item_id)
        return seen

    @staticmethod
    def _research_item_id(item: dict[str, Any]) -> str:
        key = "|".join(
            [
                str(item.get("symbol") or "").upper().strip(),
                str(item.get("source_type") or "").strip().lower(),
                str(item.get("title") or "").strip(),
                str(item.get("link") or "").strip(),
                str(item.get("published_at") or "").strip(),
            ]
        )
        return hashlib.sha1(key.encode("utf-8")).hexdigest()

    def record_cycle(self, summary: dict[str, Any], *, timestamp: datetime | None = None) -> None:
        now = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ts = now.isoformat()

        signal_map_raw = summary.get("signal_map")
        signal_map = signal_map_raw if isinstance(signal_map_raw, dict) else {}

        snapshot_event = {
            "event": "portfolio_snapshot",
            "timestamp": ts,
            "cash": float(summary.get("cash", 0.0) or 0.0),
            "account_equity": float(summary.get("account_equity", summary.get("cash", 0.0)) or 0.0),
            "equity_positions": summary.get("equity_positions") if isinstance(summary.get("equity_positions"), dict) else {},
            "option_positions": summary.get("option_positions") if isinstance(summary.get("option_positions"), dict) else {},
        }
        self._append_jsonl(self.portfolio_path, snapshot_event)

        if self.config.enable_metadata_logging:
            decision_meta_raw = summary.get("decision_metadata")
            decision_meta = decision_meta_raw if isinstance(decision_meta_raw, dict) else {}
            collection_meta_raw = summary.get("collection_metadata")
            collection_meta = collection_meta_raw if isinstance(collection_meta_raw, dict) else {}
            meta_event = {
                "event": "cycle_metadata",
                "timestamp": ts,
                "execute_orders": bool(summary.get("execute_orders", True)),
                "lookback_hours_override": summary.get("lookback_hours_override"),
                "bootstrap": summary.get("bootstrap") if isinstance(summary.get("bootstrap"), dict) else {},
                "signals_generated": int(decision_meta.get("signals_generated", 0) or 0),
                "orders_proposed": int(decision_meta.get("orders_proposed", 0) or 0),
                "no_trade_reason": str(decision_meta.get("no_trade_reason") or ""),
                "symbols_analyzed": int(collection_meta.get("symbols_analyzed", 0) or 0),
                "symbols_with_market_data": int(collection_meta.get("symbols_with_market_data", 0) or 0),
                "symbols_with_research": int(collection_meta.get("symbols_with_research", 0) or 0),
                "research_items_total": int(collection_meta.get("research_items_total", 0) or 0),
                "research_items_by_source": (
                    collection_meta.get("research_items_by_source")
                    if isinstance(collection_meta.get("research_items_by_source"), dict)
                    else {}
                ),
                "source_bias": summary.get("source_bias") if isinstance(summary.get("source_bias"), dict) else {},
                "decision_metadata": decision_meta,
                "collection_metadata": collection_meta,
            }
            self._append_jsonl(self.metadata_path, meta_event)

        research_items_raw = summary.get("research_items")
        research_items = research_items_raw if isinstance(research_items_raw, list) else []
        for raw_item in research_items:
            if not isinstance(raw_item, dict):
                continue
            item_id = self._research_item_id(raw_item)
            if item_id in self._research_seen_ids:
                continue
            self._research_seen_ids.add(item_id)
            event = {
                "event": "research_item",
                "timestamp": ts,
                "item_id": item_id,
                "symbol": str(raw_item.get("symbol") or "").upper().strip(),
                "source_type": str(raw_item.get("source_type") or "").strip().lower(),
                "source": str(raw_item.get("source") or "").strip(),
                "title": str(raw_item.get("title") or "").strip(),
                "description": str(raw_item.get("description") or "").strip(),
                "summary": str(raw_item.get("summary") or "").strip(),
                "key_points": raw_item.get("key_points") if isinstance(raw_item.get("key_points"), list) else [],
                "link": str(raw_item.get("link") or "").strip(),
                "published_at": str(raw_item.get("published_at") or "").strip(),
            }
            self._append_jsonl(self.research_path, event)

        orders = summary.get("orders")
        if not isinstance(orders, list):
            return

        for order in orders:
            if not isinstance(order, dict):
                continue

            symbol = str(order.get("symbol") or "").upper().strip()
            if not symbol:
                continue

            asset_type = str(order.get("asset_type") or "").upper().strip() or "UNKNOWN"
            underlying = option_underlying(symbol) if asset_type == "OPTION" else symbol

            signal_payload = signal_map.get(underlying)
            signal = signal_payload if isinstance(signal_payload, dict) else {}

            event = {
                "event": "trade_decision",
                "timestamp": ts,
                "symbol": symbol,
                "underlying_symbol": underlying,
                "asset_type": asset_type,
                "instruction": str(order.get("instruction") or "").upper(),
                "quantity": int(order.get("quantity") or 0),
                "limit_price": order.get("limit_price"),
                "reason": str(order.get("reason") or ""),
                "signal": signal,
            }
            self._append_jsonl(self.activity_path, event)

    def maybe_send_scheduled_reports(self, *, now: datetime | None = None) -> None:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self._maybe_send_bootstrap_optimization(current)
        self._maybe_send_layer_reevaluation(current)
        self._maybe_send_quarterly_advisor(current)
        self._maybe_send_model_roadmap_advisor(current)

        self._maybe_send_daily(current)
        self._maybe_send_weekly(current)

    @staticmethod
    def _quarter_start_for(day: date) -> date:
        month = ((day.month - 1) // 3) * 3 + 1
        return date(day.year, month, 1)

    @classmethod
    def _next_quarter_start(cls, day: date) -> date:
        start = cls._quarter_start_for(day)
        month = start.month + 3
        year = start.year
        if month > 12:
            month = 1
            year += 1
        return date(year, month, 1)

    @staticmethod
    def _quarter_index(day: date) -> int:
        return ((day.month - 1) // 3) + 1

    def _latest_report_event(self, path: Path, event_name: str) -> dict[str, Any] | None:
        rows = self._read_jsonl(path)
        for row in reversed(rows):
            if str(row.get("event") or "") == event_name and isinstance(row, dict):
                return row
        return None

    def _maybe_send_bootstrap_optimization(self, now: datetime) -> None:
        if not self.config.enable_bootstrap_optimization_reports:
            return

        now_local = now.astimezone(self.report_tz)
        if now_local.hour < self.config.bootstrap_optimization_hour_local:
            return

        report_date = now_local.date()
        report_key = report_date.isoformat()
        if self.state.last_bootstrap_optimization_date == report_key:
            return

        payload = self.build_bootstrap_optimization_digest(report_date)
        if payload is None:
            return

        self._append_jsonl(
            self.bootstrap_optimization_path,
            {
                "event": "bootstrap_optimization_report",
                "timestamp": now.isoformat(),
                "report_date": report_key,
                "subject": payload["subject"],
                "body": payload["body"],
                "metrics": payload["metrics"],
                "comparison": payload["comparison"],
                "suggestions": payload["suggestions"],
            },
        )

        logging.info(
            "Bootstrap optimization report generated for %s; stored at %s",
            report_key,
            self.bootstrap_optimization_path,
        )

        self.state.last_bootstrap_optimization_date = report_key
        self._save_state()

    def build_bootstrap_optimization_digest(self, report_date: date) -> dict[str, Any] | None:
        metadata_events = [
            event
            for event in self._read_jsonl(self.metadata_path)
            if self._event_date(event) == report_date
        ]
        bootstrap_events = [
            event
            for event in metadata_events
            if isinstance(event.get("bootstrap"), dict) and bool((event.get("bootstrap") or {}).get("active", False))
        ]
        if not bootstrap_events:
            return None

        cycle_count = len(bootstrap_events)
        avg_market_symbols = sum(
            int(event.get("symbols_with_market_data", 0) or 0) for event in bootstrap_events
        ) / cycle_count
        avg_research_symbols = sum(
            int(event.get("symbols_with_research", 0) or 0) for event in bootstrap_events
        ) / cycle_count
        avg_research_items = sum(
            int(event.get("research_items_total", 0) or 0) for event in bootstrap_events
        ) / cycle_count
        avg_signals = sum(
            int(event.get("signals_generated", 0) or 0) for event in bootstrap_events
        ) / cycle_count
        avg_orders = sum(
            int(event.get("orders_proposed", 0) or 0) for event in bootstrap_events
        ) / cycle_count
        feedback_events = sum(
            int((event.get("collection_metadata") or {}).get("historical_pattern_feedback_events", 0) or 0)
            for event in bootstrap_events
            if isinstance(event.get("collection_metadata"), dict)
        )

        source_variety_values: list[int] = []
        for event in bootstrap_events:
            by_source = event.get("research_items_by_source")
            if not isinstance(by_source, dict):
                continue
            source_variety_values.append(
                sum(1 for value in by_source.values() if isinstance(value, (int, float)) and int(value) > 0)
            )
        avg_source_variety = (
            sum(source_variety_values) / len(source_variety_values)
            if source_variety_values
            else 0.0
        )

        coverage_ratio = (avg_research_symbols / avg_market_symbols) if avg_market_symbols > 0 else 0.0
        ingestion_efficiency = self._clamp(
            (0.60 * coverage_ratio)
            + (0.25 * min(avg_source_variety / 5.0, 1.0))
            + (0.15 * min(avg_research_items / max(1.0, avg_market_symbols * 3.0), 1.0)),
            0.0,
            1.0,
        )
        reasoning_efficiency = self._clamp(
            (0.70 * min(avg_signals / max(1.0, avg_market_symbols * 0.8), 1.0))
            + (0.30 * min(feedback_events / max(1.0, cycle_count), 1.0)),
            0.0,
            1.0,
        )

        current_metrics = {
            "ingestion_efficiency": round(ingestion_efficiency, 4),
            "reasoning_efficiency": round(reasoning_efficiency, 4),
            "coverage_ratio": round(coverage_ratio, 4),
            "avg_source_variety": round(avg_source_variety, 4),
            "avg_research_items": round(avg_research_items, 4),
            "avg_signals": round(avg_signals, 4),
            "feedback_events": int(feedback_events),
            "avg_orders": round(avg_orders, 4),
            "cycles": cycle_count,
        }

        previous = self._latest_report_event(self.bootstrap_optimization_path, "bootstrap_optimization_report")
        previous_metrics = previous.get("metrics") if isinstance(previous, dict) and isinstance(previous.get("metrics"), dict) else {}
        comparison = {
            "ingestion_efficiency_delta": round(
                float(current_metrics["ingestion_efficiency"]) - float(previous_metrics.get("ingestion_efficiency", 0.0)),
                4,
            ),
            "reasoning_efficiency_delta": round(
                float(current_metrics["reasoning_efficiency"]) - float(previous_metrics.get("reasoning_efficiency", 0.0)),
                4,
            ),
            "coverage_ratio_delta": round(
                float(current_metrics["coverage_ratio"]) - float(previous_metrics.get("coverage_ratio", 0.0)),
                4,
            ),
        }

        suggestions: list[str] = []
        if coverage_ratio < 0.70:
            suggestions.append(
                "Increase ingestion breadth: raise RESEARCH_TOTAL_ITEMS_CAP or add more active source feeds to improve symbol coverage."
            )
        if avg_source_variety < 3.0:
            suggestions.append(
                "Improve source diversity: enable additional source types (social/analyst/filings) to reduce single-source concentration."
            )
        if feedback_events <= 0:
            suggestions.append(
                "Strengthen pattern learning: verify historical feedback events are being generated and consider slightly increasing HISTORICAL_RESEARCH_FEEDBACK_STRENGTH."
            )
        if avg_signals < max(1.0, avg_market_symbols * 0.20):
            suggestions.append(
                "Reasoning throughput is low: review thresholds and feature penalties to avoid excessive no-signal filtering."
            )
        if not suggestions:
            suggestions.append(
                "Current bootstrap optimization metrics are stable; keep settings unchanged and continue monitoring."
            )

        prefix = self.config.report_subject_prefix.strip() or "AI Trader"
        subject = f"[{prefix}] Bootstrap Optimization Report - {report_date.isoformat()}"
        lines: list[str] = []
        lines.append(f"Bootstrap self-optimization report for {report_date.isoformat()} (learning window active).")
        lines.append("")
        lines.append("Observed efficiency:")
        lines.append(
            f"- Ingestion efficiency: {ingestion_efficiency * 100:.1f}% "
            f"(coverage {coverage_ratio * 100:.1f}%, avg source variety {avg_source_variety:.2f})"
        )
        lines.append(
            f"- Reasoning efficiency: {reasoning_efficiency * 100:.1f}% "
            f"(avg signals/cycle {avg_signals:.2f}, historical feedback events {feedback_events})"
        )
        lines.append(
            f"- Daily cycle stats: cycles={cycle_count}, avg research items/cycle={avg_research_items:.2f}, avg proposed orders={avg_orders:.2f}"
        )
        lines.append("")
        lines.append("Day-over-day change:")
        lines.append(
            f"- Ingestion delta: {comparison['ingestion_efficiency_delta']:+.4f}, "
            f"reasoning delta: {comparison['reasoning_efficiency_delta']:+.4f}, "
            f"coverage delta: {comparison['coverage_ratio_delta']:+.4f}"
        )
        lines.append("")
        lines.append("Suggested improvements for next bootstrap day:")
        for item in suggestions:
            lines.append(f"- {item}")

        body = "\n".join(lines).strip() + "\n"
        return {
            "subject": subject,
            "body": body,
            "metrics": current_metrics,
            "comparison": comparison,
            "suggestions": suggestions,
        }

    def _maybe_send_quarterly_advisor(self, now: datetime) -> None:
        if not self.config.enable_quarterly_model_advisor:
            return

        now_local = now.astimezone(self.report_tz)
        if now_local.hour < self.config.quarterly_model_advisor_hour_local:
            return

        today = now_local.date()
        target_start = self._next_quarter_start(today)
        days_until = (target_start - today).days
        if days_until < 0 or days_until > self.config.quarterly_model_advisor_reminder_days:
            return

        target_key = target_start.isoformat()
        if self.state.last_quarterly_advisor_target == target_key:
            return

        payload = self.build_quarterly_model_advisor_payload(target_start)
        if payload is None:
            return

        subject = str(payload["subject"])
        body = str(payload["body"])
        self._append_jsonl(
            self.quarterly_advisor_path,
            {
                "event": "quarterly_model_advisor",
                "timestamp": now.isoformat(),
                "target_quarter_start": target_key,
                "subject": subject,
                "body": body,
                "metrics": payload["metrics"],
                "recommendations": payload["recommendations"],
                "comparison": payload["comparison"],
            },
        )

        logging.info(
            "Quarterly advisor generated for %s; stored at %s",
            target_key,
            self.quarterly_advisor_path,
        )

        self.state.last_quarterly_advisor_target = target_key
        self._save_state()

    def _maybe_send_model_roadmap_advisor(self, now: datetime) -> None:
        if not self.config.enable_model_roadmap_advisor:
            return

        now_local = now.astimezone(self.report_tz)
        if now_local.hour < self.config.model_roadmap_hour_local:
            return

        today = now_local.date()
        target_start = self._next_quarter_start(today)
        target_quarter = self._quarter_index(target_start)
        if target_quarter not in self.config.model_roadmap_target_quarters:
            return

        days_until = (target_start - today).days
        if days_until < 0 or days_until > self.config.model_roadmap_reminder_days:
            return

        target_key = target_start.isoformat()
        if self.state.last_model_roadmap_target == target_key:
            return

        payload = self.build_model_roadmap_advisor_payload(target_start)
        if payload is None:
            return

        subject = str(payload["subject"])
        body = str(payload["body"])
        self._append_jsonl(
            self.model_roadmap_path,
            {
                "event": "model_roadmap_advisor",
                "timestamp": now.isoformat(),
                "target_quarter_start": target_key,
                "target_quarter": target_quarter,
                "subject": subject,
                "body": body,
                "metrics": payload["metrics"],
                "recommendations": payload["recommendations"],
                "comparison": payload["comparison"],
            },
        )

        logging.info(
            "Model roadmap advisor generated for %s; stored at %s",
            target_key,
            self.model_roadmap_path,
        )

        self.state.last_model_roadmap_target = target_key
        self._save_state()

    def _maybe_send_daily(self, now: datetime) -> None:
        now_local = now.astimezone(self.report_tz)
        if now_local.hour < self.config.daily_report_hour_local:
            return

        report_date = now_local.date()
        if self.config.send_reports_market_days_only and not is_us_equity_market_day(report_date):
            return

        report_key = report_date.isoformat()
        if self.state.last_daily_report_date == report_key:
            return

        digest = self.build_daily_digest(report_date)
        if digest is None:
            return

        subject, body = digest
        self._append_jsonl(
            self.daily_report_path,
            {
                "event": "daily_report",
                "timestamp": now.isoformat(),
                "report_date": report_key,
                "subject": subject,
                "body": body,
            },
        )
        self.state.last_daily_report_date = report_key
        self._save_state()

    def _maybe_send_weekly(self, now: datetime) -> None:
        now_local = now.astimezone(self.report_tz)
        if now_local.hour < self.config.weekly_report_hour_local:
            return

        target_day = WEEKDAY_INDEX.get(self.config.weekly_report_day_local.upper(), 4)
        report_date = now_local.date()

        if self.config.send_reports_market_days_only and not is_us_equity_market_day(report_date):
            return

        send_day = self._weekly_send_day_for_week(report_date, target_day)
        if send_day is None or report_date != send_day:
            return

        end_date = report_date
        iso = end_date.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"
        if self.state.last_weekly_report_key == week_key:
            return

        digest = self.build_weekly_digest(end_date)
        if digest is None:
            return

        subject, body = digest
        self._append_jsonl(
            self.weekly_report_path,
            {
                "event": "weekly_report",
                "timestamp": now.isoformat(),
                "week_key": week_key,
                "range_end": end_date.isoformat(),
                "subject": subject,
                "body": body,
            },
        )
        self.state.last_weekly_report_key = week_key
        self._save_state()

    def _maybe_send_layer_reevaluation(self, now: datetime) -> None:
        if not self.config.enable_layer_reevaluation_reports:
            return

        now_local = now.astimezone(self.report_tz)
        if now_local.hour < self.config.quarterly_model_advisor_hour_local:
            return

        today = now_local.date()
        target_start = self._next_quarter_start(today)
        days_until = (target_start - today).days
        if days_until < 0 or days_until > self.config.quarterly_model_advisor_reminder_days:
            return

        target_key = target_start.isoformat()
        if self.state.last_layer_reevaluation_target == target_key:
            return

        payload = self.build_layer_reevaluation_payload(target_start)
        if payload is None:
            return

        metrics = payload["metrics"] if isinstance(payload.get("metrics"), dict) else {}
        self._append_jsonl(
            self.layer_reevaluation_path,
            {
                "event": "layer_reevaluation_report",
                "timestamp": now.isoformat(),
                "target_quarter_start": target_key,
                "range_start": str(metrics.get("evaluation_start") or ""),
                "range_end": str(metrics.get("evaluation_end") or ""),
                "subject": payload["subject"],
                "body": payload["body"],
                "metrics": metrics,
                "comparison": payload["comparison"],
                "recommendations": payload["recommendations"],
            },
        )
        logging.info(
            "Layer reevaluation report generated for %s; stored at %s",
            target_key,
            self.layer_reevaluation_path,
        )
        self.state.last_layer_reevaluation_target = target_key
        self._save_state()

    def _weekly_send_day_for_week(self, current_day: date, target_day_idx: int) -> date | None:
        week_start = current_day - timedelta(days=current_day.weekday())
        target_day = week_start + timedelta(days=target_day_idx)

        if current_day > target_day:
            return None

        candidates: list[date] = []
        day = week_start
        while day <= target_day:
            if (not self.config.send_reports_market_days_only) or is_us_equity_market_day(day):
                candidates.append(day)
            day += timedelta(days=1)

        if not candidates:
            return None
        return candidates[-1]

    def build_daily_digest(self, report_date: date) -> tuple[str, str] | None:
        events = [
            event
            for event in self._read_jsonl(self.activity_path)
            if self._event_date(event) == report_date
        ]
        snapshots = [
            event
            for event in self._read_jsonl(self.portfolio_path)
            if self._event_date(event) == report_date
        ]

        decision_events = [
            event
            for event in self._read_jsonl(self.decision_journal_path)
            if self._event_date(event) == report_date
        ]
        metadata_events = [
            event
            for event in self._read_jsonl(self.metadata_path)
            if self._event_date(event) == report_date
        ]

        if not events and not snapshots and not decision_events and not metadata_events:
            return None

        prefix = self.config.report_subject_prefix.strip() or "AI Trader"
        subject = f"[{prefix}] Daily Decision Digest - {report_date.isoformat()}"

        lines: list[str] = []
        lines.append(f"Daily trading and model report for {report_date.isoformat()} (UTC).")
        lines.append("")

        if snapshots:
            first_equity = float(snapshots[0].get("account_equity", 0.0) or 0.0)
            last_equity = float(snapshots[-1].get("account_equity", 0.0) or 0.0)
            delta = last_equity - first_equity
            pct = (delta / first_equity * 100.0) if first_equity > 0 else 0.0
            lines.append(
                f"Portfolio snapshot: start ${first_equity:,.2f} -> end ${last_equity:,.2f} "
                f"({delta:+,.2f}, {pct:+.2f}%)."
            )
            drawdown_pct = (
                ((first_equity - min(float(event.get("account_equity", first_equity) or first_equity) for event in snapshots)) / first_equity)
                if first_equity > 0
                else 0.0
            )
            goal_lines = self._goal_progress_lines(
                report_date=report_date,
                end_equity=last_equity,
                drawdown_pct=drawdown_pct,
            )
            if goal_lines:
                lines.extend(goal_lines)
            lines.append("")

        if metadata_events:
            lines.append("System metadata (learn/survival telemetry):")
            cycle_count = len(metadata_events)
            avg_signals = sum(int(event.get("signals_generated", 0) or 0) for event in metadata_events) / cycle_count
            trading_cycles = sum(1 for event in metadata_events if bool(event.get("execute_orders", False)))
            cycles_with_orders = sum(
                1 for event in metadata_events if int(event.get("orders_proposed", 0) or 0) > 0
            )
            no_trade_cycles = max(0, trading_cycles - cycles_with_orders)
            lines.append(
                f"- Cycles run: {cycle_count}, avg signals/cycle: {avg_signals:.2f}, "
                f"trade-capable cycles with no orders: {no_trade_cycles}"
            )

            source_counts: dict[str, int] = defaultdict(int)
            for event in metadata_events:
                by_source = event.get("research_items_by_source")
                if not isinstance(by_source, dict):
                    continue
                for key, value in by_source.items():
                    if isinstance(value, (int, float)):
                        source_counts[str(key)] += int(value)
            if source_counts:
                source_text = ", ".join(
                    f"{key}={count}" for key, count in sorted(source_counts.items(), key=lambda item: item[0])
                )
                lines.append(f"- Research coverage: {source_text}")

            latest_bias = metadata_events[-1].get("source_bias")
            if isinstance(latest_bias, dict) and latest_bias:
                ranked_bias = sorted(
                    (
                        (str(key), float(value))
                        for key, value in latest_bias.items()
                        if isinstance(value, (int, float))
                    ),
                    key=lambda item: abs(item[1]),
                    reverse=True,
                )
                if ranked_bias:
                    top_text = ", ".join(f"{key}={value:+.3f}" for key, value in ranked_bias[:6])
                    lines.append(f"- Learned source priority bias: {top_text}")
            lines.append("")

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            symbol = str(event.get("underlying_symbol") or event.get("symbol") or "UNKNOWN")
            grouped[symbol].append(event)

        if grouped:
            lines.append("Per-stock decisions:")
            for symbol in sorted(grouped.keys()):
                stock_events = grouped[symbol]
                latest = stock_events[-1]
                actions = ", ".join(
                    f"{str(event.get('instruction') or '?')} x{int(event.get('quantity') or 0)}"
                    for event in stock_events
                )
                lines.append(f"- {symbol}: {actions}")
                lines.append(f"  {self._reason_paragraph(latest)}")
            lines.append("")
        else:
            lines.append("No buy/sell orders were recorded for this day.")
            lines.append("")

        bad_calls = [
            event
            for event in decision_events
            if event.get("event") == "decision_call_resolved" and event.get("outcome") == "bad_call"
        ]
        if bad_calls:
            lines.append("Model postmortems (bad calls):")
            for event in bad_calls:
                symbol = str(event.get("symbol") or "?")
                realized = float(event.get("realized_return", 0.0) or 0.0) * 100.0
                tags = ", ".join(str(tag) for tag in (event.get("why_bad") or []))
                lines.append(f"- {symbol}: return {realized:+.2f}% | issues: {tags or 'n/a'}")
            lines.append("")

        penalty_state = self._current_feature_penalties()
        if penalty_state:
            lines.append("Current cross-ticker feature penalties:")
            for key, value in sorted(penalty_state.items()):
                lines.append(f"- {key}: {float(value):.4f}")

        body = "\n".join(lines).strip() + "\n"
        return subject, body

    def build_weekly_digest(self, end_date: date) -> tuple[str, str] | None:
        start_date = end_date - timedelta(days=6)

        snapshots = [
            event
            for event in self._read_jsonl(self.portfolio_path)
            if self._event_date_in_range(event, start_date, end_date)
        ]
        decisions = [
            event
            for event in self._read_jsonl(self.activity_path)
            if self._event_date_in_range(event, start_date, end_date)
        ]
        journal = [
            event
            for event in self._read_jsonl(self.decision_journal_path)
            if self._event_date_in_range(event, start_date, end_date)
        ]
        metadata_events = [
            event
            for event in self._read_jsonl(self.metadata_path)
            if self._event_date_in_range(event, start_date, end_date)
        ]

        if not snapshots and not decisions and not journal and not metadata_events:
            return None

        prefix = self.config.report_subject_prefix.strip() or "AI Trader"
        subject = (
            f"[{prefix}] Weekly Portfolio Summary - "
            f"{start_date.isoformat()} to {end_date.isoformat()}"
        )

        lines: list[str] = []
        lines.append(
            f"Weekly portfolio summary for {start_date.isoformat()} through {end_date.isoformat()} (UTC)."
        )
        lines.append("")

        if snapshots:
            start_equity = float(snapshots[0].get("account_equity", 0.0) or 0.0)
            end_equity = float(snapshots[-1].get("account_equity", 0.0) or 0.0)
            delta = end_equity - start_equity
            pct = (delta / start_equity * 100.0) if start_equity > 0 else 0.0
            peak = max(float(event.get("account_equity", 0.0) or 0.0) for event in snapshots)
            trough = min(float(event.get("account_equity", 0.0) or 0.0) for event in snapshots)

            lines.append(f"Start equity: ${start_equity:,.2f}")
            lines.append(f"End equity: ${end_equity:,.2f}")
            lines.append(f"Weekly change: {delta:+,.2f} ({pct:+.2f}%)")
            lines.append(f"Observed range: ${trough:,.2f} to ${peak:,.2f}")
            drawdown_pct = ((peak - trough) / peak) if peak > 0 else 0.0
            goal_lines = self._goal_progress_lines(
                report_date=end_date,
                end_equity=end_equity,
                drawdown_pct=drawdown_pct,
            )
            if goal_lines:
                lines.extend(goal_lines)
            lines.append("")

        if metadata_events:
            cycle_count = len(metadata_events)
            avg_signals = sum(int(event.get("signals_generated", 0) or 0) for event in metadata_events) / cycle_count
            orders_emitted = sum(int(event.get("orders_proposed", 0) or 0) for event in metadata_events)
            lines.append(
                f"System telemetry: {cycle_count} cycles, {orders_emitted} proposed orders, "
                f"avg signals/cycle {avg_signals:.2f}."
            )

        if decisions:
            by_symbol: dict[str, int] = defaultdict(int)
            for event in decisions:
                symbol = str(event.get("underlying_symbol") or event.get("symbol") or "UNKNOWN")
                by_symbol[symbol] += 1

            lines.append(f"Total trade decisions logged: {len(decisions)}")
            lines.append("Most active symbols:")
            for symbol, count in sorted(by_symbol.items(), key=lambda item: item[1], reverse=True)[:8]:
                lines.append(f"- {symbol}: {count} decisions")
            lines.append("")

        resolved_bad = [
            event
            for event in journal
            if event.get("event") == "decision_call_resolved" and event.get("outcome") == "bad_call"
        ]
        resolved_good = [
            event
            for event in journal
            if event.get("event") == "decision_call_resolved" and event.get("outcome") == "good_call"
        ]
        if resolved_bad or resolved_good:
            lines.append(
                "Model learning outcomes: "
                f"{len(resolved_good)} good-call reviews, {len(resolved_bad)} bad-call reviews."
            )

        body = "\n".join(lines).strip() + "\n"
        return subject, body

    def _evaluate_layer_window(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any] | None:
        snapshots = [
            event
            for event in self._read_jsonl(self.portfolio_path)
            if self._event_date_in_range(event, start_date, end_date)
        ]
        metadata_events = [
            event
            for event in self._read_jsonl(self.metadata_path)
            if self._event_date_in_range(event, start_date, end_date)
        ]
        journal = [
            event
            for event in self._read_jsonl(self.decision_journal_path)
            if self._event_date_in_range(event, start_date, end_date)
        ]
        if not snapshots and not metadata_events and not journal:
            return None

        if snapshots:
            equity_series = [float(event.get("account_equity", 0.0) or 0.0) for event in snapshots]
            start_equity = equity_series[0]
            end_equity = equity_series[-1]
            running_peak = equity_series[0]
            max_drawdown = 0.0
            for value in equity_series:
                running_peak = max(running_peak, value)
                if running_peak > 0:
                    max_drawdown = max(max_drawdown, (running_peak - value) / running_peak)
        else:
            start_equity = float(self.config.starting_capital)
            end_equity = start_equity
            max_drawdown = 0.0
        window_return_pct = ((end_equity / start_equity) - 1.0) if start_equity > 0 else 0.0

        trade_cycles = [event for event in metadata_events if bool(event.get("execute_orders", False))]
        no_trade_cycles = sum(1 for event in trade_cycles if int(event.get("orders_proposed", 0) or 0) == 0)
        no_trade_ratio = (no_trade_cycles / len(trade_cycles)) if trade_cycles else None

        llm_enabled_cycles = 0
        llm_plan_generated = 0
        llm_plan_used = 0
        llm_confidences: list[float] = []
        for event in metadata_events:
            decision = event.get("decision_metadata")
            if not isinstance(decision, dict):
                continue
            if bool(decision.get("llm_first_enabled", False)):
                llm_enabled_cycles += 1
            if bool(decision.get("llm_plan_generated", False)):
                llm_plan_generated += 1
                confidence_raw = decision.get("llm_plan_confidence")
                if isinstance(confidence_raw, (int, float)):
                    llm_confidences.append(float(confidence_raw))
            if bool(decision.get("llm_plan_used", False)):
                llm_plan_used += 1

        avg_llm_confidence = (
            sum(llm_confidences) / len(llm_confidences)
            if llm_confidences
            else None
        )
        llm_low_confidence_count = sum(
            1 for value in llm_confidences if value < float(self.config.llm_first_min_confidence)
        )
        llm_low_confidence_rate = (
            llm_low_confidence_count / len(llm_confidences)
            if llm_confidences
            else None
        )
        llm_usage_rate = (
            llm_plan_used / llm_plan_generated
            if llm_plan_generated > 0
            else None
        )
        llm_fallback_rate = (
            (llm_plan_generated - llm_plan_used) / llm_plan_generated
            if llm_plan_generated > 0
            else None
        )

        resolved = [
            event
            for event in journal
            if event.get("event") == "decision_call_resolved"
            and str(event.get("outcome") or "") in {"good_call", "bad_call"}
        ]
        good_calls = sum(1 for event in resolved if event.get("outcome") == "good_call")
        bad_calls = [event for event in resolved if event.get("outcome") == "bad_call"]
        bad_call_rate = (len(bad_calls) / len(resolved)) if resolved else None

        tag_counts: dict[str, int] = defaultdict(int)
        for event in bad_calls:
            tags = event.get("why_bad")
            if not isinstance(tags, list):
                continue
            for tag in tags:
                name = str(tag).strip()
                if name:
                    tag_counts[name] += 1

        return {
            "evaluation_start": start_date.isoformat(),
            "evaluation_end": end_date.isoformat(),
            "start_equity": start_equity,
            "end_equity": end_equity,
            "window_return_pct": window_return_pct,
            "max_drawdown_pct": max_drawdown,
            "cycles": len(metadata_events),
            "trade_cycles": len(trade_cycles),
            "no_trade_cycles": no_trade_cycles,
            "no_trade_ratio": no_trade_ratio,
            "llm_enabled_cycles": llm_enabled_cycles,
            "llm_plan_generated": llm_plan_generated,
            "llm_plan_used": llm_plan_used,
            "llm_usage_rate": llm_usage_rate,
            "llm_fallback_rate": llm_fallback_rate,
            "avg_llm_plan_confidence": avg_llm_confidence,
            "llm_low_confidence_count": llm_low_confidence_count,
            "llm_low_confidence_rate": llm_low_confidence_rate,
            "resolved_calls": len(resolved),
            "good_calls": good_calls,
            "bad_calls": len(bad_calls),
            "bad_call_rate": bad_call_rate,
            "tag_counts": dict(tag_counts),
        }

    def _recommend_layer_strengths(self, *, metrics: dict[str, Any]) -> list[dict[str, Any]]:
        sample_gate_active = (
            int(metrics.get("resolved_calls", 0) or 0) < 6
            and int(metrics.get("llm_plan_generated", 0) or 0) < 6
        )

        current = {
            "llm_first_min_confidence": float(self.config.llm_first_min_confidence),
            "ai_feedback_strength": float(self.config.ai_feedback_strength),
            "decision_learning_rate": float(self.config.decision_learning_rate),
            "source_priority_learning_rate": float(self.config.source_priority_learning_rate),
            "historical_research_weight": float(self.config.historical_research_weight),
            "historical_research_feedback_strength": float(self.config.historical_research_feedback_strength),
        }
        recommended = dict(current)
        reasons = {key: "Kept stable: metrics are mixed in this evaluation window." for key in current}

        window_return = float(metrics.get("window_return_pct", 0.0) or 0.0)
        max_drawdown = float(metrics.get("max_drawdown_pct", 0.0) or 0.0)
        no_trade_ratio = (
            float(metrics["no_trade_ratio"])
            if isinstance(metrics.get("no_trade_ratio"), (int, float))
            else None
        )
        bad_call_rate = (
            float(metrics["bad_call_rate"])
            if isinstance(metrics.get("bad_call_rate"), (int, float))
            else None
        )
        llm_low_conf_rate = (
            float(metrics["llm_low_confidence_rate"])
            if isinstance(metrics.get("llm_low_confidence_rate"), (int, float))
            else None
        )
        llm_generated = int(metrics.get("llm_plan_generated", 0) or 0)

        if sample_gate_active:
            for key in reasons:
                reasons[key] = (
                    "Insufficient samples for safe adjustment (need >=6 resolved calls or >=6 generated LLM plans)."
                )
        else:
            drawdown_limit = self._clamp(float(self.config.quarterly_goal_max_drawdown_pct), 0.0, 1.0)
            risk_stress = max_drawdown > drawdown_limit or (
                bad_call_rate is not None and bad_call_rate >= 0.60
            )
            stable_window = (
                window_return >= 0.05
                and max_drawdown <= max(0.02, drawdown_limit * 0.75)
                and (bad_call_rate is None or bad_call_rate <= 0.45)
            )

            if risk_stress:
                recommended["llm_first_min_confidence"] = self._clamp(
                    recommended["llm_first_min_confidence"] + 0.03,
                    0.25,
                    0.85,
                )
                reasons["llm_first_min_confidence"] = (
                    "Raised confidence gate after elevated drawdown/bad-call risk."
                )
                recommended["ai_feedback_strength"] = self._clamp(
                    recommended["ai_feedback_strength"] + 0.01,
                    0.02,
                    0.25,
                )
                reasons["ai_feedback_strength"] = (
                    "Increased to adapt AI thesis memory faster after wrong-way moves."
                )
                recommended["decision_learning_rate"] = self._clamp(
                    recommended["decision_learning_rate"] - 0.01,
                    0.03,
                    0.20,
                )
                reasons["decision_learning_rate"] = (
                    "Reduced to avoid over-adjusting penalties during stressed conditions."
                )
                recommended["source_priority_learning_rate"] = self._clamp(
                    recommended["source_priority_learning_rate"] - 0.01,
                    0.03,
                    0.20,
                )
                reasons["source_priority_learning_rate"] = (
                    "Reduced to prevent rapid source-weight swings under stress."
                )
                recommended["historical_research_weight"] = self._clamp(
                    recommended["historical_research_weight"] - 0.02,
                    0.10,
                    0.45,
                )
                reasons["historical_research_weight"] = (
                    "Reduced to prioritize fresh context until stability improves."
                )
                recommended["historical_research_feedback_strength"] = self._clamp(
                    recommended["historical_research_feedback_strength"] - 0.01,
                    0.05,
                    0.25,
                )
                reasons["historical_research_feedback_strength"] = (
                    "Reduced slightly to avoid overfitting short-term noise."
                )
            elif stable_window:
                recommended["llm_first_min_confidence"] = self._clamp(
                    recommended["llm_first_min_confidence"] - 0.02,
                    0.25,
                    0.85,
                )
                reasons["llm_first_min_confidence"] = (
                    "Lowered slightly to allow more LLM-driven candidates in stable conditions."
                )
                recommended["ai_feedback_strength"] = self._clamp(
                    recommended["ai_feedback_strength"] + 0.005,
                    0.02,
                    0.25,
                )
                reasons["ai_feedback_strength"] = (
                    "Raised slightly so long-term AI memory continues adapting with positive quality."
                )
                recommended["decision_learning_rate"] = self._clamp(
                    recommended["decision_learning_rate"] + 0.01,
                    0.03,
                    0.20,
                )
                reasons["decision_learning_rate"] = (
                    "Raised slightly to transfer useful cross-ticker lessons faster."
                )
                recommended["source_priority_learning_rate"] = self._clamp(
                    recommended["source_priority_learning_rate"] + 0.01,
                    0.03,
                    0.20,
                )
                reasons["source_priority_learning_rate"] = (
                    "Raised slightly to reinforce higher-quality source types."
                )
                recommended["historical_research_weight"] = self._clamp(
                    recommended["historical_research_weight"] + 0.01,
                    0.10,
                    0.45,
                )
                reasons["historical_research_weight"] = (
                    "Raised slightly to preserve useful historical context."
                )
                recommended["historical_research_feedback_strength"] = self._clamp(
                    recommended["historical_research_feedback_strength"] + 0.01,
                    0.05,
                    0.25,
                )
                reasons["historical_research_feedback_strength"] = (
                    "Raised slightly to speed event-impact learning."
                )
            else:
                if no_trade_ratio is not None and no_trade_ratio > 0.80 and (
                    bad_call_rate is None or bad_call_rate <= 0.55
                ):
                    recommended["llm_first_min_confidence"] = self._clamp(
                        recommended["llm_first_min_confidence"] - 0.01,
                        0.25,
                        0.85,
                    )
                    reasons["llm_first_min_confidence"] = (
                        "Lowered slightly because trade-capable cycles were mostly inactive."
                    )
                    recommended["decision_learning_rate"] = self._clamp(
                        recommended["decision_learning_rate"] + 0.005,
                        0.03,
                        0.20,
                    )
                    reasons["decision_learning_rate"] = (
                        "Raised slightly to increase adaptation speed in low-activity windows."
                    )
                if bad_call_rate is not None and bad_call_rate > 0.50:
                    recommended["llm_first_min_confidence"] = self._clamp(
                        recommended["llm_first_min_confidence"] + 0.02,
                        0.25,
                        0.85,
                    )
                    reasons["llm_first_min_confidence"] = (
                        "Raised because bad-call rate was elevated."
                    )
                    recommended["source_priority_learning_rate"] = self._clamp(
                        recommended["source_priority_learning_rate"] - 0.005,
                        0.03,
                        0.20,
                    )
                    reasons["source_priority_learning_rate"] = (
                        "Reduced slightly until source quality alignment improves."
                    )
                if llm_low_conf_rate is not None and llm_low_conf_rate > 0.65 and llm_generated >= 6:
                    recommended["llm_first_min_confidence"] = self._clamp(
                        recommended["llm_first_min_confidence"] + 0.01,
                        0.25,
                        0.85,
                    )
                    reasons["llm_first_min_confidence"] = (
                        "Raised because most generated plans were below the confidence floor."
                    )

        layer_rows: list[dict[str, Any]] = [
            {
                "layer": "L0",
                "layer_name": "Hard Guardrails",
                "knob": "",
                "env": "",
                "current": None,
                "recommended": None,
                "changed": False,
                "reason": "Non-tunable safety constraints. Never adjusted by learning reports.",
            }
        ]

        knob_meta = [
            ("L1", "LLM Trust Gate", "llm_first_min_confidence", "LLM_FIRST_MIN_CONFIDENCE"),
            ("L2", "AI Thesis Memory", "ai_feedback_strength", "AI_FEEDBACK_STRENGTH"),
            ("L3", "Cross-Ticker Learning", "decision_learning_rate", "DECISION_LEARNING_RATE"),
            ("L3", "Cross-Ticker Learning", "source_priority_learning_rate", "SOURCE_PRIORITY_LEARNING_RATE"),
            ("L3", "Cross-Ticker Learning", "historical_research_weight", "HISTORICAL_RESEARCH_WEIGHT"),
            (
                "L3",
                "Cross-Ticker Learning",
                "historical_research_feedback_strength",
                "HISTORICAL_RESEARCH_FEEDBACK_STRENGTH",
            ),
        ]
        for layer, layer_name, key, env in knob_meta:
            current_value = round(float(current[key]), 3)
            recommended_value = round(float(recommended[key]), 3)
            layer_rows.append(
                {
                    "layer": layer,
                    "layer_name": layer_name,
                    "knob": key,
                    "env": env,
                    "current": current_value,
                    "recommended": recommended_value,
                    "changed": abs(recommended_value - current_value) >= 0.001,
                    "reason": reasons[key],
                }
            )

        layer_rows.append(
            {
                "layer": "L4",
                "layer_name": "Execution Adaptation",
                "knob": "",
                "env": "",
                "current": 0.0,
                "recommended": 0.0,
                "changed": False,
                "reason": "Disabled until post-validation go-live. Keep execution adaptation at 0.0.",
            }
        )
        return layer_rows

    def build_layer_reevaluation_payload(self, next_quarter_start: date) -> dict[str, Any] | None:
        evaluation_end = next_quarter_start - timedelta(days=1)
        evaluation_start = self._quarter_start_for(evaluation_end)
        metrics = self._evaluate_layer_window(start_date=evaluation_start, end_date=evaluation_end)
        if metrics is None:
            return None

        recommendations = self._recommend_layer_strengths(metrics=metrics)
        previous = self._latest_report_event(self.layer_reevaluation_path, "layer_reevaluation_report")
        previous_metrics = (
            previous.get("metrics")
            if isinstance(previous, dict) and isinstance(previous.get("metrics"), dict)
            else {}
        )
        comparison = {
            "window_return_pct_delta": self._metric_delta(
                float(metrics.get("window_return_pct", 0.0) or 0.0),
                previous_metrics.get("window_return_pct", previous_metrics.get("week_return_pct")),
            ),
            "max_drawdown_pct_delta": self._metric_delta(
                float(metrics.get("max_drawdown_pct", 0.0) or 0.0),
                previous_metrics.get("max_drawdown_pct"),
            ),
            "bad_call_rate_delta": self._metric_delta(
                (
                    float(metrics["bad_call_rate"])
                    if isinstance(metrics.get("bad_call_rate"), (int, float))
                    else 0.0
                ),
                previous_metrics.get("bad_call_rate"),
            ),
            "llm_usage_rate_delta": self._metric_delta(
                (
                    float(metrics["llm_usage_rate"])
                    if isinstance(metrics.get("llm_usage_rate"), (int, float))
                    else 0.0
                ),
                previous_metrics.get("llm_usage_rate"),
            ),
            "no_trade_ratio_delta": self._metric_delta(
                (
                    float(metrics["no_trade_ratio"])
                    if isinstance(metrics.get("no_trade_ratio"), (int, float))
                    else 0.0
                ),
                previous_metrics.get("no_trade_ratio"),
            ),
        }

        prefix = self.config.report_subject_prefix.strip() or "AI Trader"
        quarter_index = self._quarter_index(next_quarter_start)
        subject = (
            f"[{prefix}] Layer Reevaluation Report - Prep for Q{quarter_index} "
            f"{next_quarter_start.year}"
        )

        lines: list[str] = []
        lines.append(
            f"Layer reevaluation for next quarter start {next_quarter_start.isoformat()}."
        )
        lines.append(
            f"Evaluation window: {metrics['evaluation_start']} through {metrics['evaluation_end']} (report timezone)."
        )
        lines.append(
            "Policy: bounded adjustments only, one review window at a time, and no automatic changes to hard guardrails."
        )
        lines.append("")
        lines.append("Observed performance:")
        lines.append(
            f"- Equity: ${float(metrics['start_equity']):,.2f} -> ${float(metrics['end_equity']):,.2f} "
            f"({float(metrics['window_return_pct']) * 100:+.2f}%)"
        )
        lines.append(f"- Max drawdown: {float(metrics['max_drawdown_pct']) * 100:.2f}%")
        lines.append(
            f"- Resolved calls: {int(metrics['resolved_calls'])} "
            f"(good={int(metrics['good_calls'])}, bad={int(metrics['bad_calls'])}"
            + (
                f", bad rate={float(metrics['bad_call_rate']) * 100:.1f}%"
                if isinstance(metrics.get("bad_call_rate"), (int, float))
                else ""
            )
            + ")"
        )
        lines.append(
            f"- LLM plans: generated={int(metrics['llm_plan_generated'])}, used={int(metrics['llm_plan_used'])}"
            + (
                f", usage rate={float(metrics['llm_usage_rate']) * 100:.1f}%"
                if isinstance(metrics.get("llm_usage_rate"), (int, float))
                else ""
            )
            + (
                f", avg confidence={float(metrics['avg_llm_plan_confidence']):.3f}"
                if isinstance(metrics.get("avg_llm_plan_confidence"), (int, float))
                else ""
            )
        )
        if isinstance(metrics.get("no_trade_ratio"), (int, float)):
            lines.append(
                f"- Trade-capable no-order cycles: {int(metrics['no_trade_cycles'])}/{int(metrics['trade_cycles'])} "
                f"({float(metrics['no_trade_ratio']) * 100:.1f}%)"
            )
        lines.append("")
        lines.append("Results vs previous layer reevaluation:")
        lines.append(
            f"- Return delta: {comparison['window_return_pct_delta']:+.4f}, "
            f"drawdown delta: {comparison['max_drawdown_pct_delta']:+.4f}, "
            f"bad-call delta: {comparison['bad_call_rate_delta']:+.4f}, "
            f"LLM usage delta: {comparison['llm_usage_rate_delta']:+.4f}, "
            f"no-trade delta: {comparison['no_trade_ratio_delta']:+.4f}"
        )
        lines.append("")
        lines.append("Layer strength recommendations:")
        for row in recommendations:
            layer = str(row.get("layer") or "")
            label = str(row.get("layer_name") or "")
            knob = str(row.get("knob") or "")
            env = str(row.get("env") or "")
            reason = str(row.get("reason") or "")
            if not knob:
                lines.append(f"- [LOCKED] {layer} {label}: {reason}")
                continue
            marker = "CHANGE" if bool(row.get("changed")) else "KEEP"
            lines.append(
                f"- [{marker}] {layer} {label} | {env}: "
                f"{float(row['current']):.3f} -> {float(row['recommended']):.3f}. {reason}"
            )

        body = "\n".join(lines).strip() + "\n"
        return {
            "subject": subject,
            "body": body,
            "metrics": metrics,
            "comparison": comparison,
            "recommendations": recommendations,
        }

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _recommend_model_strengths(
        self,
        *,
        quarter_return_pct: float,
        max_drawdown_pct: float,
        bad_call_rate: float | None,
        no_trade_ratio: float | None,
    ) -> list[dict[str, Any]]:
        current = {
            "historical_research_weight": float(self.config.historical_research_weight),
            "historical_research_feedback_strength": float(self.config.historical_research_feedback_strength),
            "decision_learning_rate": float(self.config.decision_learning_rate),
            "source_priority_learning_rate": float(self.config.source_priority_learning_rate),
            "macro_model_weight": float(self.config.macro_model_weight),
            "ai_feedback_strength": float(self.config.ai_feedback_strength),
        }
        recommended = dict(current)
        reasons = {key: "Kept stable: quarter behavior was balanced." for key in current}

        drawdown_limit = self._clamp(float(self.config.quarterly_goal_max_drawdown_pct), 0.0, 1.0)
        risk_stress = (max_drawdown_pct > drawdown_limit) or (
            bad_call_rate is not None and bad_call_rate >= 0.60
        )
        strong_quarter = (
            quarter_return_pct >= 0.05
            and max_drawdown_pct <= max(0.02, drawdown_limit * 0.75)
            and (bad_call_rate is None or bad_call_rate <= 0.45)
        )

        if risk_stress:
            recommended["historical_research_weight"] = self._clamp(
                recommended["historical_research_weight"] - 0.03,
                0.15,
                0.40,
            )
            reasons["historical_research_weight"] = (
                "Reduced to keep recent 7-day information more dominant during drawdown/high error periods."
            )
            recommended["historical_research_feedback_strength"] = self._clamp(
                recommended["historical_research_feedback_strength"] - 0.02,
                0.08,
                0.20,
            )
            reasons["historical_research_feedback_strength"] = (
                "Reduced to avoid overreacting to noisy short-term reversals."
            )
            recommended["decision_learning_rate"] = self._clamp(
                recommended["decision_learning_rate"] - 0.02,
                0.04,
                0.18,
            )
            reasons["decision_learning_rate"] = (
                "Reduced to slow cross-ticker penalty shifts while the regime is unstable."
            )
            recommended["source_priority_learning_rate"] = self._clamp(
                recommended["source_priority_learning_rate"] - 0.02,
                0.05,
                0.20,
            )
            reasons["source_priority_learning_rate"] = (
                "Reduced to prevent rapid source-weight swings under stressed conditions."
            )
            recommended["macro_model_weight"] = self._clamp(
                recommended["macro_model_weight"] - 0.02,
                0.05,
                0.20,
            )
            reasons["macro_model_weight"] = (
                "Reduced to limit macro-overrides when core signal quality is mixed."
            )
            recommended["ai_feedback_strength"] = self._clamp(
                recommended["ai_feedback_strength"] + 0.01,
                0.04,
                0.20,
            )
            reasons["ai_feedback_strength"] = (
                "Slightly increased so AI thesis memory adapts faster after wrong-way moves."
            )
        elif strong_quarter:
            recommended["historical_research_weight"] = self._clamp(
                recommended["historical_research_weight"] + 0.02,
                0.15,
                0.40,
            )
            reasons["historical_research_weight"] = (
                "Increased slightly to preserve useful historical context from a stable quarter."
            )
            recommended["historical_research_feedback_strength"] = self._clamp(
                recommended["historical_research_feedback_strength"] + 0.02,
                0.08,
                0.20,
            )
            reasons["historical_research_feedback_strength"] = (
                "Increased to learn event-impact patterns a bit faster while outcomes are reliable."
            )
            recommended["decision_learning_rate"] = self._clamp(
                recommended["decision_learning_rate"] + 0.01,
                0.04,
                0.18,
            )
            reasons["decision_learning_rate"] = (
                "Increased slightly so cross-ticker lessons are applied faster."
            )
            recommended["source_priority_learning_rate"] = self._clamp(
                recommended["source_priority_learning_rate"] + 0.01,
                0.05,
                0.20,
            )
            reasons["source_priority_learning_rate"] = (
                "Increased slightly to reinforce reliable source types."
            )
            recommended["macro_model_weight"] = self._clamp(
                recommended["macro_model_weight"] + 0.01,
                0.05,
                0.20,
            )
            reasons["macro_model_weight"] = (
                "Increased modestly since macro integration behaved well this quarter."
            )
        else:
            if no_trade_ratio is not None and no_trade_ratio > 0.80:
                recommended["macro_model_weight"] = self._clamp(
                    recommended["macro_model_weight"] + 0.01,
                    0.05,
                    0.20,
                )
                reasons["macro_model_weight"] = (
                    "Increased slightly to help break ties when too many trade-capable cycles produce no action."
                )
                recommended["decision_learning_rate"] = self._clamp(
                    recommended["decision_learning_rate"] + 0.01,
                    0.04,
                    0.18,
                )
                reasons["decision_learning_rate"] = (
                    "Increased slightly to speed adaptation in a low-activity quarter."
                )
            if bad_call_rate is not None and bad_call_rate > 0.50:
                recommended["historical_research_feedback_strength"] = self._clamp(
                    recommended["historical_research_feedback_strength"] - 0.01,
                    0.08,
                    0.20,
                )
                reasons["historical_research_feedback_strength"] = (
                    "Reduced slightly to avoid overfitting pattern feedback in mixed conditions."
                )
                recommended["source_priority_learning_rate"] = self._clamp(
                    recommended["source_priority_learning_rate"] - 0.01,
                    0.05,
                    0.20,
                )
                reasons["source_priority_learning_rate"] = (
                    "Reduced slightly until source-quality alignment improves."
                )

        order = [
            ("historical_research_weight", "Historical Research Weight"),
            ("historical_research_feedback_strength", "Historical Pattern Feedback Strength"),
            ("decision_learning_rate", "Decision Learning Rate"),
            ("source_priority_learning_rate", "Source Priority Learning Rate"),
            ("macro_model_weight", "Macro Model Weight"),
            ("ai_feedback_strength", "AI Feedback Strength"),
        ]
        rows: list[dict[str, Any]] = []
        for key, label in order:
            current_value = round(float(current[key]), 3)
            recommended_value = round(float(recommended[key]), 3)
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "current": current_value,
                    "recommended": recommended_value,
                    "changed": abs(recommended_value - current_value) >= 0.001,
                    "reason": reasons[key],
                }
            )
        return rows

    def _evaluate_quarter_window(
        self,
        *,
        evaluation_start: date,
        evaluation_end: date,
    ) -> dict[str, Any] | None:
        snapshots = [
            event
            for event in self._read_jsonl(self.portfolio_path)
            if self._event_date_in_range(event, evaluation_start, evaluation_end)
        ]
        decisions = [
            event
            for event in self._read_jsonl(self.activity_path)
            if self._event_date_in_range(event, evaluation_start, evaluation_end)
        ]
        journal = [
            event
            for event in self._read_jsonl(self.decision_journal_path)
            if self._event_date_in_range(event, evaluation_start, evaluation_end)
        ]
        metadata_events = [
            event
            for event in self._read_jsonl(self.metadata_path)
            if self._event_date_in_range(event, evaluation_start, evaluation_end)
        ]

        if not snapshots and not journal and not metadata_events:
            return None

        if snapshots:
            equity_series = [float(event.get("account_equity", 0.0) or 0.0) for event in snapshots]
            start_equity = equity_series[0]
            end_equity = equity_series[-1]
            running_peak = equity_series[0]
            max_drawdown = 0.0
            for value in equity_series:
                running_peak = max(running_peak, value)
                if running_peak > 0:
                    max_drawdown = max(max_drawdown, (running_peak - value) / running_peak)
        else:
            start_equity = float(self.config.quarterly_goal_start_equity)
            end_equity = start_equity
            max_drawdown = 0.0
        quarter_return_pct = ((end_equity / start_equity) - 1.0) if start_equity > 0 else 0.0

        resolved = [
            event
            for event in journal
            if event.get("event") == "decision_call_resolved"
            and str(event.get("outcome") or "") in {"good_call", "bad_call"}
        ]
        good_calls = sum(1 for event in resolved if event.get("outcome") == "good_call")
        bad_calls = [event for event in resolved if event.get("outcome") == "bad_call"]
        bad_call_rate = (len(bad_calls) / len(resolved)) if resolved else None

        tag_counts: dict[str, int] = defaultdict(int)
        for event in bad_calls:
            tags = event.get("why_bad")
            if not isinstance(tags, list):
                continue
            for tag in tags:
                text = str(tag).strip()
                if text:
                    tag_counts[text] += 1

        trade_cycles = [event for event in metadata_events if bool(event.get("execute_orders", False))]
        no_trade_cycles = sum(1 for event in trade_cycles if int(event.get("orders_proposed", 0) or 0) == 0)
        no_trade_ratio = (no_trade_cycles / len(trade_cycles)) if trade_cycles else None

        source_counts: dict[str, int] = defaultdict(int)
        for event in metadata_events:
            by_source = event.get("research_items_by_source")
            if not isinstance(by_source, dict):
                continue
            for key, value in by_source.items():
                if isinstance(value, (int, float)):
                    source_counts[str(key)] += int(value)
        source_total = sum(source_counts.values())
        source_concentration = (max(source_counts.values()) / source_total) if source_total > 0 else 0.0

        latest_source_bias = (
            metadata_events[-1].get("source_bias")
            if metadata_events and isinstance(metadata_events[-1], dict)
            else {}
        )
        source_bias_strength = 0.0
        if isinstance(latest_source_bias, dict):
            source_bias_strength = max(
                (
                    abs(float(value))
                    for value in latest_source_bias.values()
                    if isinstance(value, (int, float))
                ),
                default=0.0,
            )

        return {
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end": evaluation_end.isoformat(),
            "start_equity": start_equity,
            "end_equity": end_equity,
            "quarter_return_pct": quarter_return_pct,
            "max_drawdown_pct": max_drawdown,
            "decisions_logged": len(decisions),
            "resolved_calls": len(resolved),
            "good_calls": good_calls,
            "bad_calls": len(bad_calls),
            "bad_call_rate": bad_call_rate,
            "trade_cycles": len(trade_cycles),
            "no_trade_cycles": no_trade_cycles,
            "no_trade_ratio": no_trade_ratio,
            "source_concentration": source_concentration,
            "source_bias_strength": source_bias_strength,
            "source_total_items": source_total,
            "tag_counts": dict(tag_counts),
        }

    @staticmethod
    def _metric_delta(current: float, previous: Any) -> float:
        previous_value = float(previous) if isinstance(previous, (int, float)) else 0.0
        return round(current - previous_value, 4)

    def build_quarterly_model_advisor_payload(self, next_quarter_start: date) -> dict[str, Any] | None:
        evaluation_end = next_quarter_start - timedelta(days=1)
        evaluation_start = self._quarter_start_for(evaluation_end)
        metrics = self._evaluate_quarter_window(
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
        )
        if metrics is None:
            return None

        recommendations = self._recommend_model_strengths(
            quarter_return_pct=float(metrics["quarter_return_pct"]),
            max_drawdown_pct=float(metrics["max_drawdown_pct"]),
            bad_call_rate=(
                float(metrics["bad_call_rate"]) if isinstance(metrics["bad_call_rate"], (int, float)) else None
            ),
            no_trade_ratio=(
                float(metrics["no_trade_ratio"]) if isinstance(metrics["no_trade_ratio"], (int, float)) else None
            ),
        )

        previous = self._latest_report_event(self.quarterly_advisor_path, "quarterly_model_advisor")
        previous_metrics = (
            previous.get("metrics")
            if isinstance(previous, dict) and isinstance(previous.get("metrics"), dict)
            else {}
        )
        comparison = {
            "quarter_return_pct_delta": self._metric_delta(
                float(metrics["quarter_return_pct"]),
                previous_metrics.get("quarter_return_pct"),
            ),
            "max_drawdown_pct_delta": self._metric_delta(
                float(metrics["max_drawdown_pct"]),
                previous_metrics.get("max_drawdown_pct"),
            ),
            "bad_call_rate_delta": self._metric_delta(
                float(metrics["bad_call_rate"]) if isinstance(metrics["bad_call_rate"], (int, float)) else 0.0,
                previous_metrics.get("bad_call_rate"),
            ),
            "no_trade_ratio_delta": self._metric_delta(
                float(metrics["no_trade_ratio"]) if isinstance(metrics["no_trade_ratio"], (int, float)) else 0.0,
                previous_metrics.get("no_trade_ratio"),
            ),
        }

        prefix = self.config.report_subject_prefix.strip() or "AI Trader"
        quarter_index = ((next_quarter_start.month - 1) // 3) + 1
        subject = (
            f"[{prefix}] Quarterly Model Advisor - Prep for Q{quarter_index} "
            f"{next_quarter_start.year}"
        )

        lines: list[str] = []
        lines.append(
            f"Quarterly model-strength recommendation for next quarter start {next_quarter_start.isoformat()}."
        )
        lines.append(
            f"Evaluation window: {metrics['evaluation_start']} through {metrics['evaluation_end']} (report timezone)."
        )
        lines.append("")
        lines.append("Quarter performance summary:")
        lines.append(
            f"- Equity: ${float(metrics['start_equity']):,.2f} -> ${float(metrics['end_equity']):,.2f} "
            f"({float(metrics['quarter_return_pct']) * 100:+.2f}%)"
        )
        lines.append(f"- Max drawdown observed: {float(metrics['max_drawdown_pct']) * 100:.2f}%")
        lines.append(f"- Decisions logged: {int(metrics['decisions_logged'])}")
        lines.append(
            f"- Resolved calls: {int(metrics['resolved_calls'])} "
            f"(good={int(metrics['good_calls'])}, bad={int(metrics['bad_calls'])}"
            + (
                f", bad rate={float(metrics['bad_call_rate']) * 100:.1f}%"
                if isinstance(metrics["bad_call_rate"], (int, float))
                else ""
            )
            + ")"
        )
        if isinstance(metrics["no_trade_ratio"], (int, float)):
            lines.append(
                f"- Trade-capable cycles with no orders: {int(metrics['no_trade_cycles'])}/{int(metrics['trade_cycles'])} "
                f"({float(metrics['no_trade_ratio']) * 100:.1f}%)"
            )
        lines.append("")
        lines.append("Results vs previous quarterly review:")
        lines.append(
            f"- Return delta: {comparison['quarter_return_pct_delta']:+.4f}, "
            f"drawdown delta: {comparison['max_drawdown_pct_delta']:+.4f}, "
            f"bad-call delta: {comparison['bad_call_rate_delta']:+.4f}, "
            f"no-trade delta: {comparison['no_trade_ratio_delta']:+.4f}"
        )
        lines.append("")
        lines.append("Recommended model-strength changes:")
        for row in recommendations:
            marker = "CHANGE" if bool(row.get("changed")) else "KEEP"
            lines.append(
                f"- [{marker}] {row['label']}: {float(row['current']):.3f} -> {float(row['recommended']):.3f}. "
                f"{row['reason']}"
            )
        lines.append("")
        lines.append(
            "Apply updates in your .env before the next quarter starts, then restart the bot "
            "so new settings are loaded."
        )

        body = "\n".join(lines).strip() + "\n"
        return {
            "subject": subject,
            "body": body,
            "metrics": metrics,
            "recommendations": recommendations,
            "comparison": comparison,
        }

    def build_quarterly_model_advisor_digest(self, next_quarter_start: date) -> tuple[str, str] | None:
        payload = self.build_quarterly_model_advisor_payload(next_quarter_start)
        if payload is None:
            return None
        return str(payload["subject"]), str(payload["body"])

    def _recommend_new_models(
        self,
        *,
        quarter_return_pct: float,
        max_drawdown_pct: float,
        bad_call_rate: float | None,
        no_trade_ratio: float | None,
        tag_counts: dict[str, int],
        source_concentration: float,
        source_bias_strength: float,
    ) -> list[dict[str, Any]]:
        ideas: dict[str, dict[str, Any]] = {
            "regime_risk_model": {
                "label": "Regime Risk Model",
                "score": 0.0,
                "reasons": [],
                "effort": "Medium",
                "estimate": "2-3 weeks",
                "build": (
                    "Build a market-regime classifier (risk-on/risk-off/high-vol) that gates position sizing "
                    "and reduces entries during unstable regimes."
                ),
            },
            "event_impact_horizon_model": {
                "label": "Event Impact Horizon Model",
                "score": 0.0,
                "reasons": [],
                "effort": "High",
                "estimate": "3-5 weeks",
                "build": (
                    "Build an event-impact model that maps filing/news/policy event types to expected "
                    "price impact direction and time horizon."
                ),
            },
            "source_reliability_forecaster": {
                "label": "Source Reliability Forecaster",
                "score": 0.0,
                "reasons": [],
                "effort": "Medium",
                "estimate": "2-4 weeks",
                "build": (
                    "Build a source-quality forecaster that predicts which source types are likely to be "
                    "reliable by regime and ticker cluster."
                ),
            },
            "opportunity_ranking_model": {
                "label": "Opportunity Ranking Model",
                "score": 0.0,
                "reasons": [],
                "effort": "Low",
                "estimate": "1-2 weeks",
                "build": (
                    "Build an opportunity-ranking model that improves trade selection when many names are close "
                    "in score, reducing no-trade cycles."
                ),
            },
        }

        drawdown_limit = self._clamp(float(self.config.quarterly_goal_max_drawdown_pct), 0.0, 1.0)
        if max_drawdown_pct > drawdown_limit:
            ideas["regime_risk_model"]["score"] += 3.0
            ideas["regime_risk_model"]["reasons"].append(
                f"Quarter drawdown {max_drawdown_pct * 100:.2f}% exceeded limit {drawdown_limit * 100:.2f}%."
            )
        if tag_counts.get("high_volatility_regime", 0) > 0:
            ideas["regime_risk_model"]["score"] += 2.0
            ideas["regime_risk_model"]["reasons"].append(
                "Bad-call postmortems flagged high volatility regime conditions."
            )
        if bad_call_rate is not None and bad_call_rate >= 0.55:
            ideas["regime_risk_model"]["score"] += 1.0
            ideas["regime_risk_model"]["reasons"].append(
                f"Bad-call rate was elevated at {bad_call_rate * 100:.1f}%."
            )

        event_miss_count = (
            tag_counts.get("news_overreaction", 0)
            + tag_counts.get("ai_thesis_miss", 0)
            + tag_counts.get("macro_policy_miss", 0)
        )
        if event_miss_count > 0:
            ideas["event_impact_horizon_model"]["score"] += min(4.0, float(event_miss_count))
            ideas["event_impact_horizon_model"]["reasons"].append(
                f"Postmortems show {event_miss_count} event-interpretation misses "
                "(news/AI/macro timing or direction)."
            )
        if quarter_return_pct < 0:
            ideas["event_impact_horizon_model"]["score"] += 1.0
            ideas["event_impact_horizon_model"]["reasons"].append(
                "Quarter return was negative, suggesting event impact timing needs improvement."
            )

        if source_concentration > 0.55:
            ideas["source_reliability_forecaster"]["score"] += 2.0
            ideas["source_reliability_forecaster"]["reasons"].append(
                f"Research intake was concentrated ({source_concentration * 100:.1f}% from top source type)."
            )
        if source_bias_strength > 0.25:
            ideas["source_reliability_forecaster"]["score"] += 2.0
            ideas["source_reliability_forecaster"]["reasons"].append(
                f"Source-bias dispersion reached {source_bias_strength:.3f}, indicating regime-dependent reliability."
            )

        if no_trade_ratio is not None and no_trade_ratio > 0.75:
            ideas["opportunity_ranking_model"]["score"] += 3.0
            ideas["opportunity_ranking_model"]["reasons"].append(
                f"Trade-capable cycles had no orders {no_trade_ratio * 100:.1f}% of the time."
            )
        if tag_counts.get("momentum_reversal", 0) > 0:
            ideas["opportunity_ranking_model"]["score"] += 1.0
            ideas["opportunity_ranking_model"]["reasons"].append(
                "Momentum reversals appeared in bad-call diagnostics."
            )

        ranked = sorted(
            ideas.values(),
            key=lambda row: float(row["score"]),
            reverse=True,
        )
        selected = [row for row in ranked if float(row["score"]) > 0.0][:3]
        if not selected:
            selected = ranked[:2]
            for row in selected:
                if not row["reasons"]:
                    row["reasons"].append(
                        "Baseline recommendation: add targeted model diversity before next quarter."
                    )

        recommendations: list[dict[str, Any]] = []
        for row in selected:
            recommendations.append(
                {
                    "label": row["label"],
                    "score": round(float(row["score"]), 2),
                    "effort": row["effort"],
                    "estimate": row["estimate"],
                    "why": " ".join(str(reason) for reason in row["reasons"]),
                    "build": row["build"],
                }
            )
        return recommendations

    def build_model_roadmap_advisor_payload(self, next_quarter_start: date) -> dict[str, Any] | None:
        target_quarter = self._quarter_index(next_quarter_start)
        if target_quarter not in self.config.model_roadmap_target_quarters:
            return None

        evaluation_end = next_quarter_start - timedelta(days=1)
        evaluation_start = self._quarter_start_for(evaluation_end)
        metrics = self._evaluate_quarter_window(
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
        )
        if metrics is None:
            return None

        recommendations = self._recommend_new_models(
            quarter_return_pct=float(metrics["quarter_return_pct"]),
            max_drawdown_pct=float(metrics["max_drawdown_pct"]),
            bad_call_rate=(
                float(metrics["bad_call_rate"]) if isinstance(metrics["bad_call_rate"], (int, float)) else None
            ),
            no_trade_ratio=(
                float(metrics["no_trade_ratio"]) if isinstance(metrics["no_trade_ratio"], (int, float)) else None
            ),
            tag_counts=dict(metrics["tag_counts"]),
            source_concentration=float(metrics["source_concentration"]),
            source_bias_strength=float(metrics["source_bias_strength"]),
        )

        previous = self._latest_report_event(self.model_roadmap_path, "model_roadmap_advisor")
        previous_metrics = (
            previous.get("metrics")
            if isinstance(previous, dict) and isinstance(previous.get("metrics"), dict)
            else {}
        )
        previous_recommendations = (
            previous.get("recommendations")
            if isinstance(previous, dict) and isinstance(previous.get("recommendations"), list)
            else []
        )
        previous_top_priority = 0.0
        if previous_recommendations:
            first = previous_recommendations[0]
            if isinstance(first, dict) and isinstance(first.get("score"), (int, float)):
                previous_top_priority = float(first.get("score"))
        current_top_priority = float(recommendations[0]["score"]) if recommendations else 0.0
        comparison = {
            "quarter_return_pct_delta": self._metric_delta(
                float(metrics["quarter_return_pct"]),
                previous_metrics.get("quarter_return_pct"),
            ),
            "max_drawdown_pct_delta": self._metric_delta(
                float(metrics["max_drawdown_pct"]),
                previous_metrics.get("max_drawdown_pct"),
            ),
            "source_concentration_delta": self._metric_delta(
                float(metrics["source_concentration"]),
                previous_metrics.get("source_concentration"),
            ),
            "top_priority_score_delta": round(current_top_priority - previous_top_priority, 4),
        }

        prefix = self.config.report_subject_prefix.strip() or "AI Trader"
        subject = (
            f"[{prefix}] Model Roadmap Advisor - Prep for Q{target_quarter} "
            f"{next_quarter_start.year}"
        )

        lines: list[str] = []
        lines.append(
            f"Model roadmap recommendation for next quarter start {next_quarter_start.isoformat()}."
        )
        lines.append(
            f"This advisor runs for Q1/Q3 and is sent {self.config.model_roadmap_reminder_days} days before quarter start."
        )
        lines.append(
            f"Evaluation window: {metrics['evaluation_start']} through {metrics['evaluation_end']} (report timezone)."
        )
        lines.append("")
        lines.append("Learning summary from this quarter:")
        lines.append(
            f"- Equity: ${float(metrics['start_equity']):,.2f} -> ${float(metrics['end_equity']):,.2f} "
            f"({float(metrics['quarter_return_pct']) * 100:+.2f}%)"
        )
        lines.append(f"- Max drawdown: {float(metrics['max_drawdown_pct']) * 100:.2f}%")
        lines.append(f"- Decisions logged: {int(metrics['decisions_logged'])}")
        lines.append(
            f"- Resolved calls: {int(metrics['resolved_calls'])}"
            + (
                f", bad-call rate {float(metrics['bad_call_rate']) * 100:.1f}%"
                if isinstance(metrics["bad_call_rate"], (int, float))
                else ""
            )
        )
        if isinstance(metrics["no_trade_ratio"], (int, float)):
            lines.append(
                f"- Trade-capable no-order ratio: {int(metrics['no_trade_cycles'])}/{int(metrics['trade_cycles'])} "
                f"({float(metrics['no_trade_ratio']) * 100:.1f}%)"
            )
        if int(metrics["source_total_items"]) > 0:
            lines.append(
                f"- Source concentration: {float(metrics['source_concentration']) * 100:.1f}% from top source type "
                f"across {int(metrics['source_total_items'])} research items."
            )
        lines.append("")
        lines.append("Results vs previous roadmap review:")
        lines.append(
            f"- Return delta: {comparison['quarter_return_pct_delta']:+.4f}, "
            f"drawdown delta: {comparison['max_drawdown_pct_delta']:+.4f}, "
            f"source concentration delta: {comparison['source_concentration_delta']:+.4f}, "
            f"top-priority score delta: {comparison['top_priority_score_delta']:+.4f}"
        )
        lines.append("")
        lines.append("Recommended new models to build (with estimated implementation effort):")
        for row in recommendations:
            lines.append(
                f"- {row['label']} (priority {float(row['score']):.2f}, effort {row['effort']}, "
                f"estimate {row['estimate']}): {row['why']}"
            )
            lines.append(f"  Build recommendation: {row['build']}")
        lines.append("")
        lines.append(
            "Action: review these alongside the quarterly strength-adjustment report; if both arrive in the same run, apply both sets together."
        )

        body = "\n".join(lines).strip() + "\n"
        return {
            "subject": subject,
            "body": body,
            "metrics": metrics,
            "recommendations": recommendations,
            "comparison": comparison,
        }

    def build_model_roadmap_advisor_digest(self, next_quarter_start: date) -> tuple[str, str] | None:
        payload = self.build_model_roadmap_advisor_payload(next_quarter_start)
        if payload is None:
            return None
        return str(payload["subject"]), str(payload["body"])

    def _goal_progress_lines(self, *, report_date: date, end_equity: float, drawdown_pct: float) -> list[str]:
        if not self.config.enable_quarterly_goal_tracking:
            return []

        try:
            start_day = date.fromisoformat(self.config.quarterly_goal_start_date)
            end_day = date.fromisoformat(self.config.quarterly_goal_end_date)
        except ValueError:
            return []

        if report_date < start_day or report_date > end_day:
            return []

        start_equity = max(1.0, float(self.config.quarterly_goal_start_equity))
        target_equity = max(1.0, float(self.config.quarterly_goal_target_equity))
        gain_needed = target_equity - start_equity
        progress_ratio = 1.0 if gain_needed <= 0 else (end_equity - start_equity) / gain_needed
        progress_ratio = max(0.0, min(progress_ratio, 2.0))
        max_drawdown = max(0.0, min(float(self.config.quarterly_goal_max_drawdown_pct), 1.0))
        drawdown_limit_hit = drawdown_pct > max_drawdown if max_drawdown > 0 else False

        lines = [
            f"Quarter goal ({self.config.quarterly_goal_label}): "
            f"${start_equity:,.2f} -> ${target_equity:,.2f}.",
            f"Goal progress: ${end_equity:,.2f} ({progress_ratio * 100:.1f}% of planned move).",
            (
                f"Survival check: drawdown {drawdown_pct * 100:.2f}% "
                f"(limit {max_drawdown * 100:.2f}%)"
                + (" [LIMIT BREACH]" if drawdown_limit_hit else "")
            ),
        ]
        return lines

    def _reason_paragraph(self, event: dict[str, Any]) -> str:
        signal = event.get("signal") if isinstance(event.get("signal"), dict) else {}

        instruction = str(event.get("instruction") or "").upper()
        symbol = str(event.get("underlying_symbol") or event.get("symbol") or "this stock")

        score = float(signal.get("score", 0.0) or 0.0)
        momentum_20d = float(signal.get("momentum_20d", 0.0) or 0.0)
        momentum_5d = float(signal.get("momentum_5d", 0.0) or 0.0)
        trend_20d = float(signal.get("trend_20d", 0.0) or 0.0)
        news_score = float(signal.get("news_score", 0.0) or 0.0)
        current_news_score = float(signal.get("current_news_score", news_score) or news_score)
        historical_news_score = float(signal.get("historical_news_score", news_score) or news_score)
        macro_score = float(signal.get("macro_score", 0.0) or 0.0)
        ai_short = float(signal.get("ai_short_term_score", 0.0) or 0.0)
        ai_long = float(signal.get("ai_long_term_score", 0.0) or 0.0)
        volatility = float(signal.get("volatility_20d", 0.0) or 0.0)

        base = (
            f"{symbol} had 20d momentum {momentum_20d:+.2%}, 5d momentum {momentum_5d:+.2%}, "
            f"trend vs 20d average {trend_20d:+.2%}, research sentiment now/historical/blended "
            f"{current_news_score:+.2f}/{historical_news_score:+.2f}/{news_score:+.2f}, "
            f"macro policy/world-news signal {macro_score:+.2f}, "
            f"AI short/long outlook {ai_short:+.2f}/{ai_long:+.2f}, and volatility {volatility:.2f}."
        )

        if "BUY" in instruction:
            return (
                f"Buy decision: composite score reached {score:+.4f}, which met the entry criteria. {base}"
            )
        if "SELL" in instruction:
            return (
                f"Sell decision: composite score dropped to {score:+.4f} or position exceeded target sizing. {base}"
            )

        return f"Decision score at execution was {score:+.4f}. {base}"

    def _current_feature_penalties(self) -> dict[str, float]:
        path = Path(self.config.decision_learning_state_path)
        if not path.exists():
            return {}

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        penalties = payload.get("feature_penalties")
        if not isinstance(penalties, dict):
            return {}

        clean: dict[str, float] = {}
        for key, value in penalties.items():
            if isinstance(value, (int, float)):
                clean[str(key)] = float(value)
        return clean

    def _event_date(self, event: dict[str, Any]) -> date | None:
        ts = self._parse_ts(str(event.get("timestamp") or ""))
        if ts is None:
            return None
        return ts.astimezone(self.report_tz).date()

    def _event_date_in_range(self, event: dict[str, Any], start: date, end: date) -> bool:
        day = self._event_date(event)
        if day is None:
            return False
        return start <= day <= end

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
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
