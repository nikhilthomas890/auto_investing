from __future__ import annotations

import argparse
import json
import logging
import math
import shlex
import subprocess
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..control import DecisionControlCenter
from ..core.config import BotConfig
from ..dashboard import start_dashboard_server
from ..data.market_calendar import is_us_equity_market_day
from ..execution.broker import SchwabBroker
from ..learning.runtime_state import RuntimeStateStore
from ..reporting import ReportManager
from .engine import AutoTrader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI-themed Schwab auto trader")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=None,
        help="Override cycle interval (minimum 60 seconds)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live order placement. Omit this to run dry-run only.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    parser.add_argument(
        "--dashboard-only",
        action="store_true",
        help="Run only the dashboard API/UI process without trading cycles.",
    )
    return parser.parse_args()


def configure_logging(level: str, system_log_path: str = "system.log") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    path = (system_log_path or "").strip()
    if path:
        try:
            log_path = Path(path)
            if log_path.parent != Path("."):
                log_path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
        except Exception as exc:
            logging.warning("Failed to initialize system log file at %s: %s", path, exc)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)


def _catchup_lookback_hours(config: BotConfig, state: RuntimeStateStore, now_utc: datetime) -> int:
    last_pull = state.get_last_research_pull_at()
    if last_pull is None:
        return max(1, config.startup_catchup_default_hours)

    elapsed_hours = max(1, math.ceil((now_utc - last_pull).total_seconds() / 3600.0))
    return max(1, min(elapsed_hours, config.startup_catchup_max_hours))


def _time_until_seconds(now_local: datetime, target_local: datetime) -> int:
    delta = int((target_local - now_local).total_seconds())
    return max(1, delta)


def _merge_lookback_override(*values: int | None) -> int | None:
    selected = [int(value) for value in values if isinstance(value, int) and value > 0]
    if not selected:
        return None
    return max(selected)


def _next_market_day(start_day: date) -> date:
    current = start_day
    while not is_us_equity_market_day(current):
        current += timedelta(days=1)
    return current


def _bootstrap_context(
    config: BotConfig,
    state: RuntimeStateStore,
    *,
    local_day: date,
    is_market_day: bool,
) -> dict[str, object]:
    if not config.enable_first_run_bootstrap:
        return {
            "enabled": False,
            "active": False,
            "start_date_local": "",
            "trade_enable_date_local": "",
            "complete_date_local": "",
            "lookback_hours_override": None,
        }

    start_day = state.ensure_first_start_date_local(local_day)
    complete_day = state.get_bootstrap_complete_date_local()

    if complete_day is not None:
        trade_enable_day = _next_market_day(start_day + timedelta(days=config.first_run_bootstrap_days))
        return {
            "enabled": True,
            "active": False,
            "start_date_local": start_day.isoformat(),
            "trade_enable_date_local": trade_enable_day.isoformat(),
            "complete_date_local": complete_day.isoformat(),
            "lookback_hours_override": None,
        }

    trade_enable_day = _next_market_day(start_day + timedelta(days=config.first_run_bootstrap_days))
    active = True
    if is_market_day and local_day >= trade_enable_day:
        state.mark_bootstrap_complete(local_day)
        active = False
        complete_day = local_day

    return {
        "enabled": True,
        "active": active,
        "start_date_local": start_day.isoformat(),
        "trade_enable_date_local": trade_enable_day.isoformat(),
        "complete_date_local": (complete_day.isoformat() if complete_day is not None else ""),
        "lookback_hours_override": (
            config.first_run_bootstrap_lookback_hours if active else None
        ),
    }


def _run_and_record_cycle(
    trader: AutoTrader,
    reporter: ReportManager,
    state: RuntimeStateStore,
    *,
    execute_orders: bool,
    lookback_override: int | None,
    now_utc: datetime,
    bootstrap_context: dict[str, object] | None = None,
) -> None:
    summary = trader.run_cycle(
        execute_orders=execute_orders,
        lookback_hours_override=lookback_override,
    )
    if bootstrap_context:
        summary["bootstrap"] = bootstrap_context
    reporter.record_cycle(summary, timestamp=now_utc)
    reporter.maybe_send_scheduled_reports(now=now_utc)
    print(json.dumps(summary, indent=2, default=str))
    state.mark_research_pull(now_utc)


def _process_control_actions(control: DecisionControlCenter) -> dict[str, bool | int]:
    result = control.process_pending_actions()
    processed = int(result.get("processed", 0) or 0)
    restart = bool(result.get("restart_recommended", False))
    deploy = bool(result.get("deploy_recommended", False))
    if processed <= 0:
        return {
            "processed": 0,
            "restart_requested": restart,
            "deploy_requested": deploy,
        }

    logging.info(
        "Processed %d dashboard control action(s)%s%s.",
        processed,
        " [restart requested]" if restart else "",
        " [redeploy requested]" if deploy else "",
    )
    outcomes = result.get("outcomes")
    if isinstance(outcomes, list):
        for row in outcomes:
            if not isinstance(row, dict):
                continue
            logging.info(
                "Control action %s type=%s status=%s message=%s",
                str(row.get("action_id") or ""),
                str(row.get("action_type") or ""),
                str(row.get("status") or ""),
                str(row.get("message") or ""),
            )
    return {
        "processed": processed,
        "restart_requested": restart,
        "deploy_requested": deploy,
    }


def _run_redeploy_command(config: BotConfig) -> bool:
    command = (config.control_redeploy_command or "").strip()
    if not command:
        return False
    try:
        args = shlex.split(command)
    except ValueError as exc:
        logging.error("Invalid CONTROL_REDEPLOY_COMMAND: %s", exc)
        return False
    if not args:
        return False

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=max(30, int(config.control_redeploy_timeout_seconds)),
            check=False,
        )
    except Exception as exc:
        logging.exception("Redeploy command failed to run: %s", exc)
        return False

    if completed.stdout.strip():
        logging.info("Redeploy stdout: %s", completed.stdout.strip())
    if completed.stderr.strip():
        logging.warning("Redeploy stderr: %s", completed.stderr.strip())

    if completed.returncode != 0:
        logging.error("Redeploy command returned non-zero exit code: %d", completed.returncode)
        return False

    logging.info("Redeploy command completed successfully.")
    return True


def run() -> None:
    args = parse_args()
    config = BotConfig.from_env(force_live=args.live, interval_override=args.interval_seconds)
    configure_logging(args.log_level, config.system_log_path)
    soak_bootstrap_context = {
        "enabled": bool(config.enable_first_run_bootstrap),
        "active": False,
        "bypassed_by_research_soak_mode": bool(config.enable_research_soak_mode),
        "start_date_local": "",
        "trade_enable_date_local": "",
        "complete_date_local": "",
        "lookback_hours_override": None,
    }
    if config.enable_research_soak_mode:
        logging.warning(
            "Research soak mode enabled: running 24/7 research/learning cycles with order execution disabled."
        )
        if config.live_trading_requested:
            logging.warning(
                "Live trading request is ignored while ENABLE_RESEARCH_SOAK_MODE=true."
            )
    if config.live_trading_requested and not config.live_trading:
        logging.warning(
            "Live trading request was blocked. Set LIVE_TRADING_GREENLIGHT=true to allow real order placement."
        )
    elif config.live_trading:
        logging.warning("Live trading is enabled and greenlit. Real orders may be submitted.")
    control_center = DecisionControlCenter(config) if config.enable_dashboard_control else None

    if config.enable_dashboard:
        try:
            start_dashboard_server(config, control_center=control_center)
            logging.info(
                "Dashboard available at http://%s:%d",
                config.dashboard_host,
                config.dashboard_port,
            )
        except Exception as exc:
            logging.warning("Dashboard failed to start (%s:%d): %s", config.dashboard_host, config.dashboard_port, exc)

    if args.dashboard_only:
        logging.info("Dashboard-only mode enabled; trading loop is disabled.")
        poll_seconds = min(30, max(5, config.paused_poll_seconds))
        while True:
            if control_center is not None:
                try:
                    control_status = _process_control_actions(control_center)
                    if bool(control_status.get("deploy_requested", False)):
                        _run_redeploy_command(config)
                    if bool(control_status.get("restart_requested", False)) and config.control_auto_restart_on_request:
                        logging.warning("Control restart requested. Exiting process for supervisor-managed restart.")
                        return
                except Exception as exc:
                    logging.exception("Dashboard control processing failed: %s", exc)
            time.sleep(poll_seconds)

    broker = SchwabBroker(config)
    trader = AutoTrader(config, broker)
    reporter = ReportManager(config)
    runtime_state = RuntimeStateStore(config.runtime_state_path)
    runtime_tz = ZoneInfo(config.runtime_timezone)

    if args.once:
        if control_center is not None:
            control_status = _process_control_actions(control_center)
            if bool(control_status.get("deploy_requested", False)):
                _run_redeploy_command(config)
            if bool(control_status.get("restart_requested", False)) and config.control_auto_restart_on_request:
                logging.warning("Control restart requested. Exiting process for supervisor-managed restart.")
                return
        if config.enable_research_soak_mode:
            now_utc = datetime.now(timezone.utc)
            summary = trader.run_cycle(
                execute_orders=False,
                lookback_hours_override=None,
            )
            summary["bootstrap"] = soak_bootstrap_context
            reporter.record_cycle(summary, timestamp=now_utc)
            reporter.maybe_send_scheduled_reports(now=now_utc)
            print(json.dumps(summary, indent=2, default=str))
            return
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(runtime_tz)
        local_day = now_local.date()
        is_market_day = is_us_equity_market_day(local_day)
        bootstrap = _bootstrap_context(
            config,
            runtime_state,
            local_day=local_day,
            is_market_day=is_market_day,
        )
        bootstrap_lookback_once = (
            int(bootstrap.get("lookback_hours_override"))
            if isinstance(bootstrap.get("lookback_hours_override"), int)
            else None
        )
        summary = trader.run_cycle(
            execute_orders=not bool(bootstrap.get("active", False)),
            lookback_hours_override=_merge_lookback_override(
                bootstrap_lookback_once,
            ),
        )
        summary["bootstrap"] = bootstrap
        reporter.record_cycle(summary, timestamp=now_utc)
        reporter.maybe_send_scheduled_reports(now=now_utc)
        print(json.dumps(summary, indent=2, default=str))
        return

    while True:
        loop_start = time.time()
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(runtime_tz)
        local_day = now_local.date()
        is_market_day = is_us_equity_market_day(local_day)
        bootstrap = _bootstrap_context(
            config,
            runtime_state,
            local_day=local_day,
            is_market_day=is_market_day,
        )
        bootstrap_active = bool(bootstrap.get("active", False))
        bootstrap_lookback = (
            int(bootstrap.get("lookback_hours_override")) if isinstance(bootstrap.get("lookback_hours_override"), int) else None
        )

        try:
            if control_center is not None:
                control_status = _process_control_actions(control_center)
                if bool(control_status.get("deploy_requested", False)):
                    _run_redeploy_command(config)
                if bool(control_status.get("restart_requested", False)) and config.control_auto_restart_on_request:
                    logging.warning("Control restart requested. Exiting process for supervisor-managed restart.")
                    return

            if config.enable_research_soak_mode:
                _run_and_record_cycle(
                    trader,
                    reporter,
                    runtime_state,
                    execute_orders=False,
                    lookback_override=None,
                    now_utc=now_utc,
                    bootstrap_context=soak_bootstrap_context,
                )
                sleep_for = max(1, config.rebalance_interval_seconds - int(time.time() - loop_start))
                time.sleep(sleep_for)
                continue

            if bootstrap_active:
                # During first-run bootstrap, run 24/7 research-only cycles regardless of market hours.
                _run_and_record_cycle(
                    trader,
                    reporter,
                    runtime_state,
                    execute_orders=False,
                    lookback_override=bootstrap_lookback,
                    now_utc=now_utc,
                    bootstrap_context=bootstrap,
                )
                sleep_for = max(1, config.rebalance_interval_seconds - int(time.time() - loop_start))
                time.sleep(sleep_for)
                continue

            if not config.enable_market_hours_guard:
                _run_and_record_cycle(
                    trader,
                    reporter,
                    runtime_state,
                    execute_orders=not bootstrap_active,
                    lookback_override=bootstrap_lookback,
                    now_utc=now_utc,
                    bootstrap_context=bootstrap,
                )
                sleep_for = max(1, config.rebalance_interval_seconds - int(time.time() - loop_start))
                time.sleep(sleep_for)
                continue

            premarket_start = datetime.combine(
                local_day,
                dt_time(hour=config.market_premarket_start_hour_local, minute=0),
                tzinfo=runtime_tz,
            )
            market_open = datetime.combine(
                local_day,
                dt_time(
                    hour=config.market_open_hour_local,
                    minute=config.market_open_minute_local,
                ),
                tzinfo=runtime_tz,
            )
            market_close = datetime.combine(
                local_day,
                dt_time(
                    hour=config.market_close_hour_local,
                    minute=config.market_close_minute_local,
                ),
                tzinfo=runtime_tz,
            )
            runtime_shutdown = datetime.combine(
                local_day,
                dt_time(
                    hour=config.runtime_shutdown_hour_local,
                    minute=config.runtime_shutdown_minute_local,
                ),
                tzinfo=runtime_tz,
            )

            warmup_done = runtime_state.is_warmup_done_for_day(local_day)

            if now_local >= runtime_shutdown:
                reporter.maybe_send_scheduled_reports(now=now_utc)
                time.sleep(config.paused_poll_seconds)
                continue

            if not is_market_day:
                if bootstrap_active and now_local >= premarket_start:
                    _run_and_record_cycle(
                        trader,
                        reporter,
                        runtime_state,
                        execute_orders=False,
                        lookback_override=bootstrap_lookback,
                        now_utc=now_utc,
                        bootstrap_context=bootstrap,
                    )
                    elapsed = time.time() - loop_start
                    sleep_for = max(1, config.rebalance_interval_seconds - int(elapsed))
                    time.sleep(sleep_for)
                    continue

                reporter.maybe_send_scheduled_reports(now=now_utc)
                time.sleep(config.paused_poll_seconds)
                continue

            if now_local < premarket_start:
                reporter.maybe_send_scheduled_reports(now=now_utc)
                wake_in = min(
                    config.paused_poll_seconds,
                    _time_until_seconds(now_local, premarket_start),
                )
                time.sleep(wake_in)
                continue

            if now_local < market_open:
                if not warmup_done:
                    catchup_hours = _catchup_lookback_hours(config, runtime_state, now_utc)
                    logging.info(
                        "Premarket warmup cycle (%s): catch-up lookback=%dh, trading disabled",
                        now_local.isoformat(),
                        catchup_hours,
                    )
                    _run_and_record_cycle(
                        trader,
                        reporter,
                        runtime_state,
                        execute_orders=False,
                        lookback_override=_merge_lookback_override(catchup_hours, bootstrap_lookback),
                        now_utc=now_utc,
                        bootstrap_context=bootstrap,
                    )
                    runtime_state.mark_warmup_done_for_day(local_day)
                wake_in = min(
                    config.paused_poll_seconds,
                    _time_until_seconds(now_local, market_open),
                )
                time.sleep(wake_in)
                continue

            if not warmup_done:
                catchup_hours = _catchup_lookback_hours(config, runtime_state, now_utc)
                logging.info(
                    "Market open without premarket warmup. Running catch-up warmup first (%dh).",
                    catchup_hours,
                )
                _run_and_record_cycle(
                    trader,
                    reporter,
                    runtime_state,
                    execute_orders=False,
                    lookback_override=_merge_lookback_override(catchup_hours, bootstrap_lookback),
                    now_utc=now_utc,
                    bootstrap_context=bootstrap,
                )
                runtime_state.mark_warmup_done_for_day(local_day)
                time.sleep(1)
                continue

            if now_local >= market_close:
                # After-hours research pass: update signals/memory without placing trades.
                _run_and_record_cycle(
                    trader,
                    reporter,
                    runtime_state,
                    execute_orders=False,
                    lookback_override=bootstrap_lookback,
                    now_utc=now_utc,
                    bootstrap_context=bootstrap,
                )
                elapsed = time.time() - loop_start
                sleep_for = max(1, config.rebalance_interval_seconds - int(elapsed))
                time.sleep(sleep_for)
                continue

            _run_and_record_cycle(
                trader,
                reporter,
                runtime_state,
                execute_orders=not bootstrap_active,
                lookback_override=bootstrap_lookback,
                now_utc=now_utc,
                bootstrap_context=bootstrap,
            )
        except Exception as exc:
            logging.exception("Cycle failed: %s", exc)

        elapsed = time.time() - loop_start
        sleep_for = max(1, config.rebalance_interval_seconds - int(elapsed))
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
