# LLM Learning Layer Framework

This document defines the layer model for the LLM-first runtime and how layer strengths are reevaluated from live performance data.

## Layer Catalog

### L0: Hard Guardrails (Non-Tunable)

Purpose:
- enforce non-negotiable safety constraints
- prevent unsafe runtime states and unsafe order paths

Examples:
- live trading lock (`LIVE_TRADING` + `LIVE_TRADING_GREENLIGHT`)
- research soak forcing `execute_orders=false`
- blocked dashboard overrides for live-trading keys
- hard order/risk caps

Adjustment policy:
- never auto-adjusted by learning loops
- changed only by explicit code/config decisions

### L1: LLM Trust Gate

Purpose:
- control when LLM plans can be used vs fallback to deterministic rules

Primary knobs:
- `LLM_FIRST_MIN_CONFIDENCE`
- `LLM_FIRST_REQUIRE_SIGNALS_FOR_ENTRIES`
- `LLM_SUPPORT_MIN_SIGNAL_SCORE`

Adjustment policy:
- bounded and conservative
- small step deltas only

### L2: AI Thesis Memory

Purpose:
- adapt long-term AI conviction from realized outcomes

Primary knobs:
- `AI_FEEDBACK_STRENGTH`
- `AI_LONG_TERM_MEMORY_ALPHA`

Adjustment policy:
- bounded and conservative
- prioritize stability over short-term reactivity

### L3: Cross-Ticker Learning and Source Weighting

Purpose:
- transfer decision outcomes into feature penalties and source weighting
- blend current and historical research context

Primary knobs:
- `DECISION_LEARNING_RATE`
- `SOURCE_PRIORITY_LEARNING_RATE`
- `HISTORICAL_RESEARCH_WEIGHT`
- `HISTORICAL_RESEARCH_FEEDBACK_STRENGTH`

Adjustment policy:
- bounded and conservative
- reduce adaptation speed during stress; increase slightly during stable windows

### L4: Execution Adaptation (Deferred)

Purpose:
- runtime adaptation of execution behavior (timing/fill tactics/sizing dynamics)

Status:
- intentionally disabled during rewrite/validation phase

Adjustment policy:
- locked at zero until explicit go-live validation milestone

## Reevaluation Workflow

Cadence:
- weekly (aligned with weekly report schedule)
- report stored in `LAYER_REEVALUATION_LOG_PATH`

Inputs:
- portfolio snapshots (`PORTFOLIO_LOG_PATH`)
- cycle metadata (`METADATA_LOG_PATH`)
- decision outcomes (`DECISION_JOURNAL_PATH`)

Core metrics:
- weekly return and max drawdown
- resolved good/bad call rate
- LLM plan generation/use/fallback rates
- no-trade ratio in trade-capable cycles

Sample gate:
- no strength changes unless either:
  - at least 6 resolved calls, or
  - at least 6 generated LLM plans

Decision policy:
- stressed window: tighten trust gate, slow fast-adapting learners
- stable window: slightly loosen trust gate, slightly increase adaptation speed
- mixed window: only small, targeted nudges

Safety rules:
- bounded deltas per window
- no L0 modifications
- no L4 enablement by automatic report logic

## Operational Notes

- Reports are advisory; they do not self-edit `.env`.
- Apply approved recommendations manually, then restart runtime.
- Keep change batching small: prefer one review cycle between major knob updates.
