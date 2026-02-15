# AI-Themed Schwab Auto Trader

This repository contains an automated trading bot that:

- Pulls live market data from Schwab
- Pulls active research from Google News, SEC filings, earnings transcripts, social feeds, and analyst rating feeds
- Scores a layered AI universe (compute, infrastructure, software/platform, raw materials, space, optional quantum)
- Uses an LLM-first trade planner (with hard guardrails) and falls back to rules/signal-only when LLM is unavailable
- Optionally uses AI article interpretation to infer short-term and long-term outlook from recent coverage
- Rebalances equity positions using momentum + trend + research sentiment
- Adds a bullish call overlay for high-conviction names with capped options exposure
- Logs per-cycle decision metadata to support continuous model improvement
- Runs in `dry-run` mode by default

## Important

- This is not financial advice.
- Start in simulation (`LIVE_TRADING=false`) and validate behavior for multiple days.
- Options can lose 100% of premium.
- You are responsible for account permissions, compliance, and risk.
- Money-transfer endpoints are always blocked; startup fails if `RESTRICT_FUND_TRANSFERS` is not `true`.

## Project Structure

Core code is now organized by responsibility:

```text
ai_trader_bot/
  app/         # runtime entry + orchestration loop
  core/        # config + shared domain models
  control/     # dashboard-driven decision/action control center
  data/        # market/news/research/macro ingestion
  execution/   # Schwab broker execution layer
  learning/    # AI interpreter + decision feedback + runtime state
  strategy/    # signal + options logic
  reporting/   # daily/weekly/advisor report generation + storage
  dashboard/   # web UI for portfolio/research/reports/system logs
```

## Strategy at a glance

- Universe:
  - Compute layer: chips, GPUs, memory, foundries, semiconductor equipment
  - Infrastructure layer: hyperscalers, networking, power, data centers
  - Software/platform layer: AI-enabled enterprise software and tooling
  - Materials layer: copper/lithium/rare-earth exposure linked to AI infrastructure demand
  - Space layer: launch, satellite connectivity, geospatial data, and aerospace/space systems
  - Optional quantum names
- Signal score:
  - 20-day momentum
  - 5-day momentum
  - 20-day trend vs SMA
  - multi-source research sentiment (news + filings + transcripts + social + analyst updates)
  - separate macro-policy/world-news model (government policy, trade deals, geopolitics, rates) blended into each ticker score
  - optional AI short-term + long-term outlook (confidence-weighted)
  - volatility penalty
- Decision layer:
  - LLM-first planner proposes equity buys, option buys, and exits from current market/research context
  - Rule/signal engine enforces support thresholds, sizing constraints, and order caps
  - if LLM confidence is too low (or LLM unavailable), bot uses rule/signal-only flow
- Portfolio rules (aggressive defaults tuned for a $1,000 account):
  - max 6 equity names
  - max 20% in a single stock
  - 60% max equity deployment
  - 30% max options premium budget
  - max 2 open options contracts

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` values into your shell environment (or dotenv loader if you prefer).
4. Ensure your Schwab app callback URL and token path are valid.

## Cloud Deployment (VM + Dashboard)

For full production deployment (VM, dashboard HTTPS, persistent storage, and managed PostgreSQL infrastructure), use:

- `docs/CLOUD_DEPLOYMENT_AWS.md`

Included deploy templates:

- `deploy/systemd/ai-trader.service`
- `deploy/nginx/ai-trader-dashboard.conf`

### Runtime Schedule Guard (Daily 6 PM Pause + 7 AM Startup + Weekend Pause)

By default, continuous mode now runs with market-time guardrails:

- Pauses daily from 6:00 PM ET to 7:00 AM ET (state files remain intact)
- On market days at 7:00 AM ET, runs one warmup cycle with no orders
- Warmup cycle expands lookback to catch data since last pull (for weekend catch-up)
- Trading decisions/orders run only during market hours (9:30 AM to 4:00 PM ET by default)
- On weekends/market holidays, remains paused and resumes next market day at startup hour
  - Exception: during first-run bootstrap, it can run research-only cycles on non-market days to build context

Key settings:

- `ENABLE_MARKET_HOURS_GUARD=true`
- `RUNTIME_TIMEZONE=America/New_York`
- `MARKET_PREMARKET_START_HOUR_LOCAL=7`
- `MARKET_OPEN_HOUR_LOCAL=9`, `MARKET_OPEN_MINUTE_LOCAL=30`
- `MARKET_CLOSE_HOUR_LOCAL=16`, `MARKET_CLOSE_MINUTE_LOCAL=0`
- `RUNTIME_SHUTDOWN_HOUR_LOCAL=18`, `RUNTIME_SHUTDOWN_MINUTE_LOCAL=0`
- `RUNTIME_STATE_PATH=runtime_state.json`
- `STARTUP_CATCHUP_DEFAULT_HOURS=72`
- `STARTUP_CATCHUP_MAX_HOURS=120`

### First-Run Bootstrap (Learning-Only Window)

The first time runtime state is created, the bot can run in learning-only mode before it is allowed to trade:

- No orders are placed during bootstrap
- Bootstrap runs 24 hours/day (including overnight and weekends) to keep ingesting/learning
- It uses a long lookback (default 6 months) to build context from historical coverage
- Trading is enabled on the first market day on/after `start_date + 5 calendar days`
  - if day 5 is a non-market day, it waits until the next market day

Controls:

- `ENABLE_FIRST_RUN_BOOTSTRAP=true`
- `FIRST_RUN_BOOTSTRAP_DAYS=5`
- `FIRST_RUN_BOOTSTRAP_LOOKBACK_HOURS=4320` (about 6 months)

### LLM-First Decisioning + AI Interpreter

The runtime is LLM-first by default:

- `ENABLE_LLM_FIRST_DECISIONING=true`
- `LLM_FIRST_MAX_SYMBOLS=12`
- `LLM_FIRST_MIN_CONFIDENCE=0.35`
- `LLM_FIRST_REQUIRE_SIGNALS_FOR_ENTRIES=true`
- `LLM_SUPPORT_MIN_SIGNAL_SCORE=0.0`

LLM-first behavior:

- LLM proposes `equity_buy_symbols`, `option_buy_symbols`, and `exit_symbols`
- hard risk controls still apply (position sizing, max orders, options budget, max contracts)
- entry proposals can require supporting signal scores (`LLM_FIRST_REQUIRE_SIGNALS_FOR_ENTRIES`)
- if no API key/provider mismatch/low confidence, bot automatically falls back to the deterministic rules/signal engine

Optional AI article interpretation (separate from LLM decision planner):

- `ENABLE_AI_NEWS_INTERPRETER` is on by default
- Provide `OPENAI_API_KEY`
- Tune `AI_SHORT_TERM_WEIGHT`, `AI_LONG_TERM_WEIGHT`, and `AI_LONG_TERM_MEMORY_ALPHA`
- Keep `ENABLE_AI_FEEDBACK_LEARNING=true` to adjust long-term conviction after wrong-way moves
- Tune `AI_FEEDBACK_STRENGTH` to control how quickly it adapts after wins/losses
- Keep `ENABLE_DECISION_LEARNING=true` to run postmortems on calls and transfer mistakes across tickers

The bot stores long-term AI thesis memory in `AI_LONG_TERM_STATE_PATH` as an EMA score per symbol.

### Multi-Source Research Feeds

The model can ingest additional context beyond headlines:

- Full article text from Google News links (`ENABLE_FULL_ARTICLE_TEXT=true`)
- SEC filings from EDGAR (`ENABLE_SEC_FILINGS=true`)
- Earnings call transcripts (`ENABLE_EARNINGS_TRANSCRIPTS=true`, requires `FMP_API_KEY`)
- Trusted social feeds via RSS (`ENABLE_SOCIAL_FEEDS=true`, configure `SOCIAL_FEED_RSS_URLS` and `TRUSTED_SOCIAL_ACCOUNTS`)
- Analyst rating feeds (`ENABLE_ANALYST_RATINGS=true`, optional `FMP_API_KEY` / `FINNHUB_API_KEY`)

Useful feed settings:

- `RESEARCH_ITEMS_PER_SOURCE`
- `RESEARCH_TOTAL_ITEMS_CAP`
- `DECISION_RESEARCH_LOOKBACK_HOURS=168` (minimum 7-day context used for live decisions)
- `ENABLE_HISTORICAL_RESEARCH_MEMORY=true`
- `HISTORICAL_RESEARCH_MEMORY_ALPHA=0.15`
- `HISTORICAL_RESEARCH_WEIGHT=0.25` (keeps 7-day research dominant)
- `ENABLE_HISTORICAL_RESEARCH_FEEDBACK_LEARNING=true`
- `HISTORICAL_RESEARCH_FEEDBACK_STRENGTH=0.12`
- `SEC_FILINGS_LOOKBACK_HOURS`
- `EARNINGS_TRANSCRIPT_LOOKBACK_HOURS`
- `SOCIAL_FEED_LOOKBACK_HOURS`
- `ANALYST_RATING_LOOKBACK_HOURS`

With historical research memory enabled, each symbol's current-cycle sentiment is blended with stored historical sentiment. The model also learns event-impact patterns by checking whether prior research signals matched subsequent price moves.

### Macro Policy / World-News Model

In addition to per-ticker research, the bot runs a separate macro model focused on policy and global events:

- Government decisions/policy
- Trade deals and export controls
- Geopolitics and sanctions
- Rates/inflation/fiscal signals

The macro model generates a score and blends it into each ticker's final signal.

Controls:

- `ENABLE_MACRO_POLICY_MODEL=true`
- `MACRO_POLICY_QUERY=...`
- `MACRO_NEWS_LOOKBACK_HOURS=24`
- `MACRO_NEWS_ITEMS=20`
- `MACRO_MODEL_WEIGHT=0.10` (impact on each ticker score)
- `MACRO_HEADLINE_WEIGHT=0.70`
- `MACRO_AI_SHORT_TERM_WEIGHT=0.15`
- `MACRO_AI_LONG_TERM_WEIGHT=0.15`
- `MACRO_LONG_TERM_STATE_PATH=macro_long_term_state.json`
- `MACRO_LONG_TERM_MEMORY_ALPHA=0.20`

### Decision Journal and Cross-Ticker Learning

The bot can log and learn from bad calls:

- It writes each opened/closed call to `DECISION_JOURNAL_PATH` (JSONL).
- It stores open calls + learned feature penalties in `DECISION_LEARNING_STATE_PATH`.
- After `DECISION_EVAL_HORIZON_HOURS`, it scores the call outcome using realized return:
  - bad: `return <= BAD_CALL_RETURN_THRESHOLD`
  - good: `return >= GOOD_CALL_RETURN_THRESHOLD`
- For bad calls, it increases penalties on the drivers that pushed the call (momentum/news/AI/etc).
- Those penalties reduce future scores for all tickers when the same driver pattern appears.

### Source Priority Learning

The bot can learn which source types are more reliable over time:

- Sources covered: `news`, `sec_filing`, `earnings_transcript`, `social`, `analyst_rating`
- Learns from:
  - post-trade outcomes after the evaluation horizon, and
  - market reaction outcomes (price change vs prior cycle) even when no trade was placed
- Learns a per-source bias that scales source sentiment impact in future cycles

Controls:

- `ENABLE_SOURCE_PRIORITY_LEARNING=true`
- `SOURCE_PRIORITY_LEARNING_RATE=0.18`
- `MAX_SOURCE_RELIABILITY_BIAS=0.80`
- `ENABLE_SOURCE_MARKET_REACTION_LEARNING=true`
- `SOURCE_MARKET_REACTION_STRENGTH=0.35`

### Quarterly Goal Tracking

You can track quarter-based targets with survival constraints:

- `ENABLE_QUARTERLY_GOAL_TRACKING=true`
- `QUARTERLY_GOAL_LABEL=Q1 2026 Survival and Learn`
- `QUARTERLY_GOAL_START_DATE=2026-01-01`
- `QUARTERLY_GOAL_END_DATE=2026-03-31`
- `QUARTERLY_GOAL_START_EQUITY=1000`
- `QUARTERLY_GOAL_TARGET_EQUITY=1500`
- `QUARTERLY_GOAL_MAX_DRAWDOWN_PCT=0.20`

Daily/weekly digests include progress against this goal.

### Cycle Metadata Logging

The bot writes per-cycle telemetry to help diagnose/learn from behavior:

- `METADATA_LOG_PATH` (default `metadata_log.jsonl`)
- `ENABLE_METADATA_LOGGING=true`

Logged fields include signal counts, proposed orders, no-trade reasons, and research source coverage by type.

### Dashboard-First Reporting

Email delivery is removed. Reports are generated and stored as logs, then displayed in the web dashboard.

Dashboard pages:

- Portfolio: current holdings and open call positions
- Research Feed: daily news/filings/transcripts/social/analyst items with summary, key points, and source link
- Reports: tabbed daily/weekly/quarterly/roadmap/bootstrap reports
- To-Do: implementation backlog and planned strategy/infrastructure improvements
- System Logs: live runtime log view
- Control Center: submit runtime decisions (value updates, model build requests, restart/redeploy requests) and view action results

Configure scheduling/report generation:

- `REPORT_TIMEZONE` (default `America/New_York`)
- `DAILY_REPORT_HOUR_LOCAL` (default `18`)
- `WEEKLY_REPORT_DAY_LOCAL` (`MON`..`SUN`, default `FRI`)
- `WEEKLY_REPORT_HOUR_LOCAL` (default `18`)
- `SEND_REPORTS_MARKET_DAYS_ONLY=true`
- `ENABLE_QUARTERLY_MODEL_ADVISOR=true`
- `QUARTERLY_MODEL_ADVISOR_REMINDER_DAYS=5`
- `QUARTERLY_MODEL_ADVISOR_HOUR_LOCAL=18`
- `ENABLE_MODEL_ROADMAP_ADVISOR=true`
- `MODEL_ROADMAP_TARGET_QUARTERS=Q1,Q3`
- `MODEL_ROADMAP_REMINDER_DAYS=14`
- `MODEL_ROADMAP_HOUR_LOCAL=18`
- `ENABLE_BOOTSTRAP_OPTIMIZATION_REPORTS=true`
- `BOOTSTRAP_OPTIMIZATION_HOUR_LOCAL=18`

Configure dashboard:

- `ENABLE_DASHBOARD=true`
- `DASHBOARD_HOST=127.0.0.1`
- `DASHBOARD_PORT=8787`
- `DASHBOARD_RESEARCH_ITEMS_PER_CYCLE=120`
- `ENABLE_DASHBOARD_CONTROL=true`
- `CONTROL_ACTIONS_LOG_PATH=control_actions.jsonl`
- `CONTROL_RESULTS_LOG_PATH=control_results.jsonl`
- `RUNTIME_OVERRIDES_PATH=runtime_overrides.json`
- `MODEL_BUILD_REQUESTS_PATH=model_build_requests.jsonl`
- `CONTROL_MAX_ACTIONS_PER_CYCLE=20`
- `CONTROL_AUTO_APPLY_ON_SUBMIT=true`
- `CONTROL_AUTO_RESTART_ON_REQUEST=false` (if `true`, runtime exits when restart is requested so a supervisor can restart it)
- `CONTROL_REDEPLOY_COMMAND=` (optional shell command run when redeploy is requested)
- `CONTROL_REDEPLOY_TIMEOUT_SECONDS=900`

Report/runtime data paths:

- `REPORT_STATE_PATH`
- `DAILY_REPORT_LOG_PATH`
- `WEEKLY_REPORT_LOG_PATH`
- `QUARTERLY_MODEL_ADVISOR_LOG_PATH`
- `MODEL_ROADMAP_LOG_PATH`
- `BOOTSTRAP_OPTIMIZATION_LOG_PATH`
- `RESEARCH_LOG_PATH`
- `ACTIVITY_LOG_PATH`
- `PORTFOLIO_LOG_PATH`
- `METADATA_LOG_PATH`
- `SYSTEM_LOG_PATH`

## First run (safe)

Run one dry-run cycle:

```bash
python -m ai_trader_bot --once
```

Run continuous dry-run:

```bash
python -m ai_trader_bot --interval-seconds 300
```

Run dashboard only (no trading loop):

```bash
python -m ai_trader_bot --dashboard-only
```

Default dashboard URL: `http://127.0.0.1:8787`.

## Live trading

Only after validation, enable live execution:

```bash
python -m ai_trader_bot --live --interval-seconds 300
```

`--live` overrides `LIVE_TRADING=false` and allows real order submission.

## Notes on Schwab auth

This bot uses `schwab-py` authentication and order templates.

- Schwab-py docs: https://schwab-py.readthedocs.io/en/latest/
- Schwab developer portal: https://developer.schwab.com/

On first auth, a browser login/consent flow may be required to create `token.json`.

## Tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```
