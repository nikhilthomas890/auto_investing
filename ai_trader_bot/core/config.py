from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_csv(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default or [])
    values = [chunk.strip() for chunk in raw.split(",")]
    return [value for value in values if value]


def _env_quarters(name: str, default: list[int]) -> list[int]:
    tokens = _env_csv(name, [f"Q{value}" for value in default])
    parsed: list[int] = []
    for token in tokens:
        text = token.strip().upper()
        if text.startswith("Q"):
            text = text[1:]
        if not text.isdigit():
            continue
        value = int(text)
        if value < 1 or value > 4:
            continue
        if value not in parsed:
            parsed.append(value)
    return parsed or list(default)


DEFAULT_COMPUTE_UNIVERSE = [
    "NVDA",
    "AMD",
    "AVGO",
    "TSM",
    "ASML",
    "MU",
    "ARM",
    "MRVL",
    "AMAT",
    "LRCX",
]

DEFAULT_INFRA_UNIVERSE = [
    "MSFT",
    "AMZN",
    "GOOGL",
    "META",
    "ANET",
    "SMCI",
    "DELL",
    "VRT",
    "EQIX",
    "DLR",
    "ETN",
    "CEG",
]

DEFAULT_SOFTWARE_UNIVERSE = [
    "ORCL",
    "SNOW",
    "PLTR",
    "CRM",
    "NOW",
    "MDB",
    "DDOG",
    "NET",
    "ADBE",
]

DEFAULT_MATERIALS_UNIVERSE = [
    "FCX",
    "SCCO",
    "MP",
    "ALB",
    "SQM",
]

DEFAULT_SPACE_UNIVERSE = [
    "RKLB",
    "ASTS",
    "IRDM",
    "SPIR",
    "PL",
    "LMT",
    "NOC",
    "RTX",
]

DEFAULT_UNIVERSE = list(
    dict.fromkeys(
        DEFAULT_COMPUTE_UNIVERSE
        + DEFAULT_INFRA_UNIVERSE
        + DEFAULT_SOFTWARE_UNIVERSE
        + DEFAULT_MATERIALS_UNIVERSE
        + DEFAULT_SPACE_UNIVERSE
    )
)

DEFAULT_QUANTUM = ["IONQ", "RGTI", "QBTS"]


@dataclass
class BotConfig:
    starting_capital: float = 1000.0
    live_trading: bool = False
    live_trading_requested: bool = False
    live_trading_greenlight: bool = False
    enable_research_soak_mode: bool = False
    rebalance_interval_seconds: int = 300
    enable_market_hours_guard: bool = True
    runtime_timezone: str = "America/New_York"
    market_premarket_start_hour_local: int = 7
    market_open_hour_local: int = 9
    market_open_minute_local: int = 30
    market_close_hour_local: int = 16
    market_close_minute_local: int = 0
    runtime_shutdown_hour_local: int = 18
    runtime_shutdown_minute_local: int = 0
    paused_poll_seconds: int = 120
    runtime_state_path: str = "runtime_state.json"
    startup_catchup_default_hours: int = 72
    startup_catchup_max_hours: int = 120
    enable_first_run_bootstrap: bool = True
    first_run_bootstrap_days: int = 5
    first_run_bootstrap_lookback_hours: int = 4320

    universe: list[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    include_quantum: bool = True

    max_equity_positions: int = 6
    equity_capital_fraction: float = 0.60
    max_position_fraction: float = 0.20
    min_signal_to_enter: float = 0.012
    signal_to_exit: float = -0.018

    enable_options: bool = True
    option_capital_fraction: float = 0.30
    option_signal_threshold: float = 0.035
    option_min_dte: int = 14
    option_max_dte: int = 45
    option_target_delta: float = 0.45
    max_option_contracts: int = 2

    min_order_notional: float = 25.0
    max_orders_per_cycle: int = 8

    news_lookback_hours: int = 6
    news_items_per_symbol: int = 10
    decision_research_lookback_hours: int = 168
    enable_historical_research_memory: bool = True
    historical_research_state_path: str = "historical_research_state.json"
    historical_research_memory_alpha: float = 0.15
    historical_research_weight: float = 0.25
    enable_historical_research_feedback_learning: bool = True
    historical_research_feedback_strength: float = 0.12
    research_items_per_source: int = 6
    research_total_items_cap: int = 24
    enable_full_article_text: bool = True
    article_text_max_chars: int = 3500

    enable_sec_filings: bool = True
    sec_filings_lookback_hours: int = 72
    sec_forms: list[str] = field(default_factory=lambda: ["10-Q", "10-K", "8-K", "20-F", "6-K"])
    sec_user_agent: str = "ai-autotrader/0.2 (research)"

    enable_earnings_transcripts: bool = True
    earnings_transcript_lookback_hours: int = 336
    earnings_transcript_max_chars: int = 5000

    enable_social_feeds: bool = False
    social_feed_lookback_hours: int = 24
    social_feed_rss_urls: list[str] = field(default_factory=list)
    trusted_social_accounts: list[str] = field(default_factory=list)

    enable_analyst_ratings: bool = True
    analyst_rating_lookback_hours: int = 720

    enable_macro_policy_model: bool = True
    macro_policy_query: str = (
        "US government policy regulation tariffs trade deals export controls sanctions "
        "geopolitics fiscal policy central bank interest rates inflation"
    )
    macro_news_lookback_hours: int = 24
    macro_news_items: int = 20
    macro_model_weight: float = 0.10
    macro_headline_weight: float = 0.70
    macro_ai_short_term_weight: float = 0.15
    macro_ai_long_term_weight: float = 0.15
    macro_long_term_state_path: str = "macro_long_term_state.json"
    macro_long_term_memory_alpha: float = 0.20

    enable_ai_news_interpreter: bool = True
    ai_provider: str = "openai"
    ai_model_name: str = "gpt-4o-mini"
    ai_api_key: str = ""
    ai_timeout_seconds: float = 20.0
    enable_llm_first_decisioning: bool = True
    llm_first_max_symbols: int = 12
    llm_first_min_confidence: float = 0.35
    llm_first_require_signals_for_entries: bool = True
    llm_support_min_signal_score: float = 0.0
    ai_short_term_weight: float = 0.10
    ai_long_term_weight: float = 0.15
    ai_long_term_memory_alpha: float = 0.20
    ai_long_term_state_path: str = "long_term_state.json"
    enable_ai_feedback_learning: bool = True
    ai_feedback_strength: float = 0.06

    enable_decision_learning: bool = True
    decision_learning_state_path: str = "decision_learning_state.json"
    decision_journal_path: str = "decision_journal.jsonl"
    decision_evaluation_horizon_hours: int = 48
    bad_call_return_threshold: float = -0.03
    good_call_return_threshold: float = 0.03
    decision_learning_rate: float = 0.07
    max_feature_penalty: float = 0.45
    enable_source_priority_learning: bool = True
    source_priority_learning_rate: float = 0.10
    max_source_reliability_bias: float = 0.40
    enable_source_market_reaction_learning: bool = True
    source_market_reaction_strength: float = 0.20

    report_subject_prefix: str = "AI Trader"
    report_timezone: str = "America/New_York"
    daily_report_hour_local: int = 18
    weekly_report_day_local: str = "FRI"
    weekly_report_hour_local: int = 18
    send_reports_market_days_only: bool = True
    enable_quarterly_model_advisor: bool = True
    quarterly_model_advisor_reminder_days: int = 5
    quarterly_model_advisor_hour_local: int = 18
    quarterly_model_advisor_log_path: str = "quarterly_model_advisor.jsonl"
    enable_model_roadmap_advisor: bool = True
    model_roadmap_target_quarters: list[int] = field(default_factory=lambda: [1, 3])
    model_roadmap_reminder_days: int = 14
    model_roadmap_hour_local: int = 18
    model_roadmap_log_path: str = "model_roadmap_advisor.jsonl"
    enable_bootstrap_optimization_reports: bool = True
    bootstrap_optimization_hour_local: int = 18
    bootstrap_optimization_log_path: str = "bootstrap_optimization_report.jsonl"
    enable_layer_reevaluation_reports: bool = True
    layer_reevaluation_log_path: str = "layer_reevaluation_report.jsonl"
    report_state_path: str = "report_state.json"
    daily_report_log_path: str = "daily_report.jsonl"
    weekly_report_log_path: str = "weekly_report.jsonl"
    research_log_path: str = "research_log.jsonl"
    activity_log_path: str = "activity_log.jsonl"
    portfolio_log_path: str = "portfolio_log.jsonl"
    metadata_log_path: str = "metadata_log.jsonl"
    system_log_path: str = "system.log"
    enable_dashboard: bool = True
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8787
    dashboard_research_items_per_cycle: int = 120
    enable_dashboard_control: bool = True
    control_actions_log_path: str = "control_actions.jsonl"
    control_results_log_path: str = "control_results.jsonl"
    runtime_overrides_path: str = "runtime_overrides.json"
    model_build_requests_path: str = "model_build_requests.jsonl"
    control_max_actions_per_cycle: int = 20
    control_auto_apply_on_submit: bool = True
    control_auto_restart_on_request: bool = False
    control_redeploy_command: str = ""
    control_redeploy_timeout_seconds: int = 900
    enable_metadata_logging: bool = True

    enable_quarterly_goal_tracking: bool = True
    quarterly_goal_label: str = "Q1 2026 Survival and Learn"
    quarterly_goal_start_date: str = "2026-01-01"
    quarterly_goal_end_date: str = "2026-03-31"
    quarterly_goal_start_equity: float = 1000.0
    quarterly_goal_target_equity: float = 1500.0
    quarterly_goal_max_drawdown_pct: float = 0.20

    request_timeout_seconds: float = 8.0

    restrict_fund_transfers: bool = True

    fmp_api_key: str = ""
    finnhub_api_key: str = ""

    schwab_api_key: str = ""
    schwab_app_secret: str = ""
    schwab_callback_url: str = "https://127.0.0.1:8182"
    schwab_token_path: str = "token.json"
    schwab_account_number: str | None = None

    @classmethod
    def from_env(cls, *, force_live: bool | None = None, interval_override: int | None = None) -> "BotConfig":
        symbols = os.getenv("AI_UNIVERSE", "")
        if symbols.strip():
            universe = [chunk.strip().upper() for chunk in symbols.split(",") if chunk.strip()]
        else:
            universe = list(DEFAULT_UNIVERSE)

        include_quantum = _env_bool("INCLUDE_QUANTUM", True)
        if include_quantum:
            for symbol in DEFAULT_QUANTUM:
                if symbol not in universe:
                    universe.append(symbol)

        live_trading_requested = _env_bool("LIVE_TRADING", False)
        if force_live is not None:
            live_trading_requested = force_live
        live_trading_greenlight = _env_bool("LIVE_TRADING_GREENLIGHT", False)
        live_trading = bool(live_trading_requested and live_trading_greenlight)
        enable_research_soak_mode = _env_bool("ENABLE_RESEARCH_SOAK_MODE", False)
        if enable_research_soak_mode:
            # Research-soak mode always disables real order submission.
            live_trading = False

        interval = _env_int("REBALANCE_INTERVAL_SECONDS", 300)
        if interval_override is not None:
            interval = interval_override

        return cls(
            starting_capital=_env_float("STARTING_CAPITAL", 1000.0),
            live_trading=live_trading,
            live_trading_requested=live_trading_requested,
            live_trading_greenlight=live_trading_greenlight,
            enable_research_soak_mode=enable_research_soak_mode,
            rebalance_interval_seconds=max(interval, 60),
            enable_market_hours_guard=_env_bool("ENABLE_MARKET_HOURS_GUARD", True),
            runtime_timezone=os.getenv("RUNTIME_TIMEZONE", "America/New_York").strip() or "America/New_York",
            market_premarket_start_hour_local=min(
                max(_env_int("MARKET_PREMARKET_START_HOUR_LOCAL", 7), 0),
                23,
            ),
            market_open_hour_local=min(max(_env_int("MARKET_OPEN_HOUR_LOCAL", 9), 0), 23),
            market_open_minute_local=min(max(_env_int("MARKET_OPEN_MINUTE_LOCAL", 30), 0), 59),
            market_close_hour_local=min(max(_env_int("MARKET_CLOSE_HOUR_LOCAL", 16), 0), 23),
            market_close_minute_local=min(max(_env_int("MARKET_CLOSE_MINUTE_LOCAL", 0), 0), 59),
            runtime_shutdown_hour_local=min(max(_env_int("RUNTIME_SHUTDOWN_HOUR_LOCAL", 18), 0), 23),
            runtime_shutdown_minute_local=min(max(_env_int("RUNTIME_SHUTDOWN_MINUTE_LOCAL", 0), 0), 59),
            paused_poll_seconds=max(10, _env_int("PAUSED_POLL_SECONDS", 120)),
            runtime_state_path=os.getenv("RUNTIME_STATE_PATH", "runtime_state.json").strip()
            or "runtime_state.json",
            startup_catchup_default_hours=max(1, _env_int("STARTUP_CATCHUP_DEFAULT_HOURS", 72)),
            startup_catchup_max_hours=max(1, _env_int("STARTUP_CATCHUP_MAX_HOURS", 120)),
            enable_first_run_bootstrap=_env_bool("ENABLE_FIRST_RUN_BOOTSTRAP", True),
            first_run_bootstrap_days=max(0, _env_int("FIRST_RUN_BOOTSTRAP_DAYS", 5)),
            first_run_bootstrap_lookback_hours=max(24, _env_int("FIRST_RUN_BOOTSTRAP_LOOKBACK_HOURS", 4320)),
            universe=universe,
            include_quantum=include_quantum,
            max_equity_positions=max(1, _env_int("MAX_EQUITY_POSITIONS", 6)),
            equity_capital_fraction=min(max(_env_float("EQUITY_CAPITAL_FRACTION", 0.60), 0.0), 1.0),
            max_position_fraction=min(max(_env_float("MAX_POSITION_FRACTION", 0.20), 0.0), 1.0),
            min_signal_to_enter=_env_float("MIN_SIGNAL_TO_ENTER", 0.012),
            signal_to_exit=_env_float("SIGNAL_TO_EXIT", -0.018),
            enable_options=_env_bool("ENABLE_OPTIONS", True),
            option_capital_fraction=min(max(_env_float("OPTION_CAPITAL_FRACTION", 0.30), 0.0), 1.0),
            option_signal_threshold=_env_float("OPTION_SIGNAL_THRESHOLD", 0.035),
            option_min_dte=max(1, _env_int("OPTION_MIN_DTE", 14)),
            option_max_dte=max(1, _env_int("OPTION_MAX_DTE", 45)),
            option_target_delta=min(max(_env_float("OPTION_TARGET_DELTA", 0.45), 0.0), 1.0),
            max_option_contracts=max(0, _env_int("MAX_OPTION_CONTRACTS", 2)),
            min_order_notional=max(1.0, _env_float("MIN_ORDER_NOTIONAL", 25.0)),
            max_orders_per_cycle=max(1, _env_int("MAX_ORDERS_PER_CYCLE", 8)),
            news_lookback_hours=max(1, _env_int("NEWS_LOOKBACK_HOURS", 6)),
            news_items_per_symbol=max(1, _env_int("NEWS_ITEMS_PER_SYMBOL", 10)),
            decision_research_lookback_hours=max(1, _env_int("DECISION_RESEARCH_LOOKBACK_HOURS", 168)),
            enable_historical_research_memory=_env_bool("ENABLE_HISTORICAL_RESEARCH_MEMORY", True),
            historical_research_state_path=os.getenv(
                "HISTORICAL_RESEARCH_STATE_PATH",
                "historical_research_state.json",
            ).strip()
            or "historical_research_state.json",
            historical_research_memory_alpha=min(max(_env_float("HISTORICAL_RESEARCH_MEMORY_ALPHA", 0.15), 0.0), 1.0),
            historical_research_weight=min(max(_env_float("HISTORICAL_RESEARCH_WEIGHT", 0.25), 0.0), 1.0),
            enable_historical_research_feedback_learning=_env_bool(
                "ENABLE_HISTORICAL_RESEARCH_FEEDBACK_LEARNING",
                True,
            ),
            historical_research_feedback_strength=min(
                max(_env_float("HISTORICAL_RESEARCH_FEEDBACK_STRENGTH", 0.12), 0.0),
                1.0,
            ),
            research_items_per_source=max(1, _env_int("RESEARCH_ITEMS_PER_SOURCE", 6)),
            research_total_items_cap=max(1, _env_int("RESEARCH_TOTAL_ITEMS_CAP", 24)),
            enable_full_article_text=_env_bool("ENABLE_FULL_ARTICLE_TEXT", True),
            article_text_max_chars=max(200, _env_int("ARTICLE_TEXT_MAX_CHARS", 3500)),
            enable_sec_filings=_env_bool("ENABLE_SEC_FILINGS", True),
            sec_filings_lookback_hours=max(1, _env_int("SEC_FILINGS_LOOKBACK_HOURS", 72)),
            sec_forms=[
                form.strip().upper()
                for form in _env_csv("SEC_FORMS", ["10-Q", "10-K", "8-K", "20-F", "6-K"])
                if form.strip()
            ],
            sec_user_agent=os.getenv("SEC_USER_AGENT", "ai-autotrader/0.2 (research)").strip()
            or "ai-autotrader/0.2 (research)",
            enable_earnings_transcripts=_env_bool("ENABLE_EARNINGS_TRANSCRIPTS", True),
            earnings_transcript_lookback_hours=max(1, _env_int("EARNINGS_TRANSCRIPT_LOOKBACK_HOURS", 336)),
            earnings_transcript_max_chars=max(200, _env_int("EARNINGS_TRANSCRIPT_MAX_CHARS", 5000)),
            enable_social_feeds=_env_bool("ENABLE_SOCIAL_FEEDS", False),
            social_feed_lookback_hours=max(1, _env_int("SOCIAL_FEED_LOOKBACK_HOURS", 24)),
            social_feed_rss_urls=_env_csv("SOCIAL_FEED_RSS_URLS", []),
            trusted_social_accounts=_env_csv("TRUSTED_SOCIAL_ACCOUNTS", []),
            enable_analyst_ratings=_env_bool("ENABLE_ANALYST_RATINGS", True),
            analyst_rating_lookback_hours=max(1, _env_int("ANALYST_RATING_LOOKBACK_HOURS", 720)),
            enable_macro_policy_model=_env_bool("ENABLE_MACRO_POLICY_MODEL", True),
            macro_policy_query=os.getenv(
                "MACRO_POLICY_QUERY",
                (
                    "US government policy regulation tariffs trade deals export controls sanctions "
                    "geopolitics fiscal policy central bank interest rates inflation"
                ),
            ).strip(),
            macro_news_lookback_hours=max(1, _env_int("MACRO_NEWS_LOOKBACK_HOURS", 24)),
            macro_news_items=max(1, _env_int("MACRO_NEWS_ITEMS", 20)),
            macro_model_weight=min(max(_env_float("MACRO_MODEL_WEIGHT", 0.10), 0.0), 1.0),
            macro_headline_weight=min(max(_env_float("MACRO_HEADLINE_WEIGHT", 0.70), 0.0), 1.0),
            macro_ai_short_term_weight=min(max(_env_float("MACRO_AI_SHORT_TERM_WEIGHT", 0.15), 0.0), 1.0),
            macro_ai_long_term_weight=min(max(_env_float("MACRO_AI_LONG_TERM_WEIGHT", 0.15), 0.0), 1.0),
            macro_long_term_state_path=os.getenv(
                "MACRO_LONG_TERM_STATE_PATH",
                "macro_long_term_state.json",
            ).strip()
            or "macro_long_term_state.json",
            macro_long_term_memory_alpha=min(max(_env_float("MACRO_LONG_TERM_MEMORY_ALPHA", 0.20), 0.0), 1.0),
            enable_ai_news_interpreter=_env_bool("ENABLE_AI_NEWS_INTERPRETER", True),
            ai_provider=os.getenv("AI_PROVIDER", "openai").strip().lower() or "openai",
            ai_model_name=os.getenv("AI_MODEL_NAME", "gpt-4o-mini").strip() or "gpt-4o-mini",
            ai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            ai_timeout_seconds=max(2.0, _env_float("AI_TIMEOUT_SECONDS", 20.0)),
            enable_llm_first_decisioning=_env_bool("ENABLE_LLM_FIRST_DECISIONING", True),
            llm_first_max_symbols=max(1, _env_int("LLM_FIRST_MAX_SYMBOLS", 12)),
            llm_first_min_confidence=min(max(_env_float("LLM_FIRST_MIN_CONFIDENCE", 0.35), 0.0), 1.0),
            llm_first_require_signals_for_entries=_env_bool("LLM_FIRST_REQUIRE_SIGNALS_FOR_ENTRIES", True),
            llm_support_min_signal_score=_env_float("LLM_SUPPORT_MIN_SIGNAL_SCORE", 0.0),
            ai_short_term_weight=min(max(_env_float("AI_SHORT_TERM_WEIGHT", 0.10), 0.0), 1.0),
            ai_long_term_weight=min(max(_env_float("AI_LONG_TERM_WEIGHT", 0.15), 0.0), 1.0),
            ai_long_term_memory_alpha=min(max(_env_float("AI_LONG_TERM_MEMORY_ALPHA", 0.20), 0.0), 1.0),
            ai_long_term_state_path=os.getenv("AI_LONG_TERM_STATE_PATH", "long_term_state.json").strip()
            or "long_term_state.json",
            enable_ai_feedback_learning=_env_bool("ENABLE_AI_FEEDBACK_LEARNING", True),
            ai_feedback_strength=min(max(_env_float("AI_FEEDBACK_STRENGTH", 0.06), 0.0), 1.0),
            enable_decision_learning=_env_bool("ENABLE_DECISION_LEARNING", True),
            decision_learning_state_path=os.getenv(
                "DECISION_LEARNING_STATE_PATH",
                "decision_learning_state.json",
            ).strip()
            or "decision_learning_state.json",
            decision_journal_path=os.getenv(
                "DECISION_JOURNAL_PATH",
                "decision_journal.jsonl",
            ).strip()
            or "decision_journal.jsonl",
            decision_evaluation_horizon_hours=max(1, _env_int("DECISION_EVAL_HORIZON_HOURS", 48)),
            bad_call_return_threshold=_env_float("BAD_CALL_RETURN_THRESHOLD", -0.03),
            good_call_return_threshold=_env_float("GOOD_CALL_RETURN_THRESHOLD", 0.03),
            decision_learning_rate=min(max(_env_float("DECISION_LEARNING_RATE", 0.07), 0.0), 1.0),
            max_feature_penalty=max(0.0, _env_float("MAX_FEATURE_PENALTY", 0.45)),
            enable_source_priority_learning=_env_bool("ENABLE_SOURCE_PRIORITY_LEARNING", True),
            source_priority_learning_rate=min(
                max(_env_float("SOURCE_PRIORITY_LEARNING_RATE", 0.10), 0.0),
                1.0,
            ),
            max_source_reliability_bias=max(0.0, _env_float("MAX_SOURCE_RELIABILITY_BIAS", 0.40)),
            enable_source_market_reaction_learning=_env_bool("ENABLE_SOURCE_MARKET_REACTION_LEARNING", True),
            source_market_reaction_strength=min(
                max(_env_float("SOURCE_MARKET_REACTION_STRENGTH", 0.20), 0.0),
                1.0,
            ),
            report_subject_prefix=os.getenv("REPORT_SUBJECT_PREFIX", "AI Trader").strip() or "AI Trader",
            report_timezone=os.getenv("REPORT_TIMEZONE", "America/New_York").strip() or "America/New_York",
            daily_report_hour_local=min(
                max(_env_int("DAILY_REPORT_HOUR_LOCAL", _env_int("DAILY_REPORT_HOUR_UTC", 18)), 0),
                23,
            ),
            weekly_report_day_local=(
                os.getenv("WEEKLY_REPORT_DAY_LOCAL", os.getenv("WEEKLY_REPORT_DAY_UTC", "FRI")).strip().upper()
                or "FRI"
            ),
            weekly_report_hour_local=min(
                max(_env_int("WEEKLY_REPORT_HOUR_LOCAL", _env_int("WEEKLY_REPORT_HOUR_UTC", 18)), 0),
                23,
            ),
            send_reports_market_days_only=_env_bool("SEND_REPORTS_MARKET_DAYS_ONLY", True),
            enable_quarterly_model_advisor=_env_bool("ENABLE_QUARTERLY_MODEL_ADVISOR", True),
            quarterly_model_advisor_reminder_days=max(1, _env_int("QUARTERLY_MODEL_ADVISOR_REMINDER_DAYS", 5)),
            quarterly_model_advisor_hour_local=min(
                max(_env_int("QUARTERLY_MODEL_ADVISOR_HOUR_LOCAL", 18), 0),
                23,
            ),
            quarterly_model_advisor_log_path=os.getenv(
                "QUARTERLY_MODEL_ADVISOR_LOG_PATH",
                "quarterly_model_advisor.jsonl",
            ).strip()
            or "quarterly_model_advisor.jsonl",
            enable_model_roadmap_advisor=_env_bool("ENABLE_MODEL_ROADMAP_ADVISOR", True),
            model_roadmap_target_quarters=_env_quarters("MODEL_ROADMAP_TARGET_QUARTERS", [1, 3]),
            model_roadmap_reminder_days=max(1, _env_int("MODEL_ROADMAP_REMINDER_DAYS", 14)),
            model_roadmap_hour_local=min(
                max(_env_int("MODEL_ROADMAP_HOUR_LOCAL", 18), 0),
                23,
            ),
            model_roadmap_log_path=os.getenv(
                "MODEL_ROADMAP_LOG_PATH",
                "model_roadmap_advisor.jsonl",
            ).strip()
            or "model_roadmap_advisor.jsonl",
            enable_bootstrap_optimization_reports=_env_bool("ENABLE_BOOTSTRAP_OPTIMIZATION_REPORTS", True),
            bootstrap_optimization_hour_local=min(
                max(_env_int("BOOTSTRAP_OPTIMIZATION_HOUR_LOCAL", 18), 0),
                23,
            ),
            bootstrap_optimization_log_path=os.getenv(
                "BOOTSTRAP_OPTIMIZATION_LOG_PATH",
                "bootstrap_optimization_report.jsonl",
            ).strip()
            or "bootstrap_optimization_report.jsonl",
            enable_layer_reevaluation_reports=_env_bool("ENABLE_LAYER_REEVALUATION_REPORTS", True),
            layer_reevaluation_log_path=os.getenv(
                "LAYER_REEVALUATION_LOG_PATH",
                "layer_reevaluation_report.jsonl",
            ).strip()
            or "layer_reevaluation_report.jsonl",
            report_state_path=os.getenv("REPORT_STATE_PATH", "report_state.json").strip()
            or "report_state.json",
            daily_report_log_path=os.getenv("DAILY_REPORT_LOG_PATH", "daily_report.jsonl").strip()
            or "daily_report.jsonl",
            weekly_report_log_path=os.getenv("WEEKLY_REPORT_LOG_PATH", "weekly_report.jsonl").strip()
            or "weekly_report.jsonl",
            research_log_path=os.getenv("RESEARCH_LOG_PATH", "research_log.jsonl").strip()
            or "research_log.jsonl",
            activity_log_path=os.getenv("ACTIVITY_LOG_PATH", "activity_log.jsonl").strip() or "activity_log.jsonl",
            portfolio_log_path=os.getenv("PORTFOLIO_LOG_PATH", "portfolio_log.jsonl").strip()
            or "portfolio_log.jsonl",
            metadata_log_path=os.getenv("METADATA_LOG_PATH", "metadata_log.jsonl").strip() or "metadata_log.jsonl",
            system_log_path=os.getenv("SYSTEM_LOG_PATH", "system.log").strip() or "system.log",
            enable_dashboard=_env_bool("ENABLE_DASHBOARD", True),
            dashboard_host=os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1",
            dashboard_port=max(1, _env_int("DASHBOARD_PORT", 8787)),
            dashboard_research_items_per_cycle=max(10, _env_int("DASHBOARD_RESEARCH_ITEMS_PER_CYCLE", 120)),
            enable_dashboard_control=_env_bool("ENABLE_DASHBOARD_CONTROL", True),
            control_actions_log_path=os.getenv("CONTROL_ACTIONS_LOG_PATH", "control_actions.jsonl").strip()
            or "control_actions.jsonl",
            control_results_log_path=os.getenv("CONTROL_RESULTS_LOG_PATH", "control_results.jsonl").strip()
            or "control_results.jsonl",
            runtime_overrides_path=os.getenv("RUNTIME_OVERRIDES_PATH", "runtime_overrides.json").strip()
            or "runtime_overrides.json",
            model_build_requests_path=os.getenv("MODEL_BUILD_REQUESTS_PATH", "model_build_requests.jsonl").strip()
            or "model_build_requests.jsonl",
            control_max_actions_per_cycle=max(1, _env_int("CONTROL_MAX_ACTIONS_PER_CYCLE", 20)),
            control_auto_apply_on_submit=_env_bool("CONTROL_AUTO_APPLY_ON_SUBMIT", True),
            control_auto_restart_on_request=_env_bool("CONTROL_AUTO_RESTART_ON_REQUEST", False),
            control_redeploy_command=os.getenv("CONTROL_REDEPLOY_COMMAND", "").strip(),
            control_redeploy_timeout_seconds=max(30, _env_int("CONTROL_REDEPLOY_TIMEOUT_SECONDS", 900)),
            enable_metadata_logging=_env_bool("ENABLE_METADATA_LOGGING", True),
            enable_quarterly_goal_tracking=_env_bool("ENABLE_QUARTERLY_GOAL_TRACKING", True),
            quarterly_goal_label=os.getenv("QUARTERLY_GOAL_LABEL", "Q1 2026 Survival and Learn").strip()
            or "Q1 2026 Survival and Learn",
            quarterly_goal_start_date=os.getenv("QUARTERLY_GOAL_START_DATE", "2026-01-01").strip() or "2026-01-01",
            quarterly_goal_end_date=os.getenv("QUARTERLY_GOAL_END_DATE", "2026-03-31").strip() or "2026-03-31",
            quarterly_goal_start_equity=max(1.0, _env_float("QUARTERLY_GOAL_START_EQUITY", 1000.0)),
            quarterly_goal_target_equity=max(1.0, _env_float("QUARTERLY_GOAL_TARGET_EQUITY", 1500.0)),
            quarterly_goal_max_drawdown_pct=min(
                max(_env_float("QUARTERLY_GOAL_MAX_DRAWDOWN_PCT", 0.20), 0.0),
                1.0,
            ),
            request_timeout_seconds=max(1.0, _env_float("REQUEST_TIMEOUT_SECONDS", 8.0)),
            restrict_fund_transfers=_env_bool("RESTRICT_FUND_TRANSFERS", True),
            fmp_api_key=os.getenv("FMP_API_KEY", "").strip(),
            finnhub_api_key=os.getenv("FINNHUB_API_KEY", "").strip(),
            schwab_api_key=os.getenv("SCHWAB_API_KEY", "").strip(),
            schwab_app_secret=os.getenv("SCHWAB_APP_SECRET", "").strip(),
            schwab_callback_url=os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182").strip(),
            schwab_token_path=os.getenv("SCHWAB_TOKEN_PATH", "token.json").strip(),
            schwab_account_number=os.getenv("SCHWAB_ACCOUNT_NUMBER", "").strip() or None,
        )
