from __future__ import annotations

import logging
import statistics
from dataclasses import asdict
from typing import Any

from ..core.config import BotConfig
from ..core.models import PortfolioSnapshot, Signal, TradeOrder
from ..data.macro import MacroPolicyModel
from ..data.news import source_weighted_sentiment
from ..data.research import collect_research_items
from ..data.universe import build_theme_map
from ..execution.broker import SchwabBroker
from ..learning.ai_interpreter import (
    LLMDecisionPlan,
    LongTermMemoryStore,
    OpenAIDecisionPlanner,
    OpenAINewsInterpreter,
)
from ..learning.decision_learning import DecisionLearningStore, signal_feature_profile
from ..strategy.options import choose_bullish_call, option_underlying
from ..strategy.signals import compute_signal_with_ai


class AutoTrader:
    def __init__(self, config: BotConfig, broker: SchwabBroker) -> None:
        self.config = config
        self.broker = broker
        self.theme_map = build_theme_map(config.universe, config.include_quantum)
        self.ai_interpreter = OpenAINewsInterpreter(config)
        self.llm_decision_planner = OpenAIDecisionPlanner(config)
        self.macro_model = MacroPolicyModel(config, self.ai_interpreter)
        self.long_term_memory = LongTermMemoryStore(
            config.ai_long_term_state_path,
            config.ai_long_term_memory_alpha,
        )
        self.historical_research_memory = (
            LongTermMemoryStore(
                config.historical_research_state_path,
                config.historical_research_memory_alpha,
            )
            if config.enable_historical_research_memory
            else None
        )
        self.decision_learning = (
            DecisionLearningStore(
                state_path=config.decision_learning_state_path,
                journal_path=config.decision_journal_path,
                evaluation_horizon_hours=config.decision_evaluation_horizon_hours,
                bad_call_return_threshold=config.bad_call_return_threshold,
                good_call_return_threshold=config.good_call_return_threshold,
                learning_rate=config.decision_learning_rate,
                max_feature_penalty=config.max_feature_penalty,
                enable_source_priority_learning=config.enable_source_priority_learning,
                source_learning_rate=config.source_priority_learning_rate,
                max_source_bias=config.max_source_reliability_bias,
                market_reaction_strength=config.source_market_reaction_strength,
            )
            if config.enable_decision_learning
            else None
        )

    def _build_decision_metadata(
        self,
        *,
        signals: list[Signal],
        orders: list[TradeOrder],
        account_equity: float,
        execute_orders: bool,
        llm_plan: LLMDecisionPlan | None = None,
        llm_plan_used: bool = False,
    ) -> dict[str, Any]:
        scores = [signal.score for signal in signals]
        order_by_asset: dict[str, int] = {}
        order_by_instruction: dict[str, int] = {}
        for order in orders:
            order_by_asset[order.asset_type] = order_by_asset.get(order.asset_type, 0) + 1
            order_by_instruction[order.instruction] = order_by_instruction.get(order.instruction, 0) + 1

        top_signal = signals[0] if signals else None
        score_stats = {
            "avg": (statistics.fmean(scores) if scores else 0.0),
            "median": (statistics.median(scores) if scores else 0.0),
            "max": (max(scores) if scores else 0.0),
            "min": (min(scores) if scores else 0.0),
        }

        no_trade_reason = ""
        if not execute_orders:
            no_trade_reason = "execution_disabled"
        elif not orders:
            if not signals:
                no_trade_reason = "no_valid_signals"
            elif score_stats["max"] < self.config.min_signal_to_enter:
                no_trade_reason = "scores_below_entry_threshold"
            else:
                no_trade_reason = "risk_or_sizing_constraints"

        return {
            "account_equity": account_equity,
            "signals_generated": len(signals),
            "equity_entry_candidates": sum(
                1 for signal in signals if signal.score >= self.config.min_signal_to_enter
            ),
            "option_entry_candidates": sum(
                1 for signal in signals if signal.score >= self.config.option_signal_threshold
            ),
            "orders_proposed": len(orders),
            "orders_by_asset_type": order_by_asset,
            "orders_by_instruction": order_by_instruction,
            "score_stats": score_stats,
            "top_signal_symbol": (top_signal.symbol if top_signal is not None else ""),
            "top_signal_score": (top_signal.score if top_signal is not None else 0.0),
            "no_trade_reason": no_trade_reason,
            "llm_first_enabled": bool(self.config.enable_llm_first_decisioning),
            "llm_plan_generated": llm_plan is not None,
            "llm_plan_used": llm_plan_used,
            "llm_plan_confidence": (llm_plan.confidence if llm_plan is not None else 0.0),
            "llm_plan_summary": (llm_plan.summary if llm_plan is not None else ""),
            "llm_plan_equity_buy_symbols": (llm_plan.equity_buy_symbols if llm_plan is not None else []),
            "llm_plan_option_buy_symbols": (llm_plan.option_buy_symbols if llm_plan is not None else []),
            "llm_plan_exit_symbols": (llm_plan.exit_symbols if llm_plan is not None else []),
        }

    @staticmethod
    def _effective_lookback(base_hours: int, override_hours: int | None) -> int:
        if override_hours is None:
            return max(1, base_hours)
        return max(1, int(override_hours))

    @staticmethod
    def _decision_window_lookback(min_hours: int, override_hours: int | None) -> int:
        minimum = max(1, int(min_hours))
        if override_hours is None:
            return minimum
        return max(minimum, int(override_hours))

    @staticmethod
    def _blend_news_with_history(*, current_news_score: float, historical_news_score: float, history_weight: float) -> float:
        weight = max(0.0, min(history_weight, 1.0))
        blended = ((1.0 - weight) * current_news_score) + (weight * historical_news_score)
        return max(-1.0, min(1.0, blended))

    @staticmethod
    def _compact_research_summary(*, title: str, description: str, content: str) -> str:
        text = (content or description or title or "").strip()
        if not text:
            return ""
        text = " ".join(text.split())
        if len(text) <= 260:
            return text
        return text[:257].rstrip() + "..."

    @staticmethod
    def _compact_key_points(*, title: str, description: str, content: str) -> list[str]:
        candidates: list[str] = []
        for raw in (title, description):
            value = (raw or "").strip()
            if value:
                candidates.append(value)
        if content:
            for chunk in content.replace("\\n", ". ").split("."):
                text = chunk.strip()
                if len(text) >= 30:
                    candidates.append(text)
                if len(candidates) >= 6:
                    break

        points: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            cleaned = " ".join(candidate.split())
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            if len(cleaned) > 180:
                cleaned = cleaned[:177].rstrip() + "..."
            points.append(cleaned)
            if len(points) >= 4:
                break
        return points

    def _signal_supports_llm_entry(self, signal: Signal) -> bool:
        if not self.config.llm_first_require_signals_for_entries:
            return True
        return signal.score >= self.config.llm_support_min_signal_score

    def _build_llm_symbol_context(
        self,
        *,
        signals: list[Signal],
        snapshot: PortfolioSnapshot,
        research_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        max_symbols = max(1, int(self.config.llm_first_max_symbols))
        research_by_symbol: dict[str, list[str]] = {}
        for item in research_items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            summary = str(item.get("summary") or item.get("description") or "").strip()
            if not summary:
                continue
            bucket = research_by_symbol.setdefault(symbol, [])
            if len(bucket) >= 4:
                continue
            bucket.append(summary[:360])

        held_symbols = {symbol.upper() for symbol in snapshot.equity_positions.keys()}
        held_symbols.update(
            option_underlying(symbol)
            for symbol, quantity in snapshot.option_positions.items()
            if quantity > 0
        )

        selected_symbols: list[str] = []
        for signal in signals:
            if signal.symbol not in selected_symbols:
                selected_symbols.append(signal.symbol)
            if len(selected_symbols) >= max_symbols:
                break
        for symbol in sorted(held_symbols):
            if symbol in selected_symbols:
                continue
            selected_symbols.append(symbol)
            if len(selected_symbols) >= max_symbols:
                break

        signals_by_symbol = {signal.symbol: signal for signal in signals}
        rows: list[dict[str, Any]] = []
        for symbol in selected_symbols:
            signal = signals_by_symbol.get(symbol)
            if signal is None:
                continue
            rows.append(
                {
                    "symbol": signal.symbol,
                    "score": signal.score,
                    "momentum_20d": signal.momentum_20d,
                    "momentum_5d": signal.momentum_5d,
                    "trend_20d": signal.trend_20d,
                    "volatility_20d": signal.volatility_20d,
                    "news_score": signal.news_score,
                    "macro_score": signal.macro_score,
                    "recent_research": research_by_symbol.get(signal.symbol, [])[:3],
                }
            )
        return rows

    def _generate_llm_plan(
        self,
        *,
        snapshot: PortfolioSnapshot,
        signals: list[Signal],
        research_items: list[dict[str, Any]],
    ) -> LLMDecisionPlan | None:
        if not self.config.enable_llm_first_decisioning:
            return None
        if not self.llm_decision_planner.enabled:
            return None

        context_rows = self._build_llm_symbol_context(
            signals=signals,
            snapshot=snapshot,
            research_items=research_items,
        )
        if not context_rows:
            return None

        held_equities = [symbol for symbol, quantity in snapshot.equity_positions.items() if quantity > 0]
        held_option_underlyings = [
            option_underlying(symbol)
            for symbol, quantity in snapshot.option_positions.items()
            if quantity > 0
        ]
        return self.llm_decision_planner.build_plan(
            symbol_contexts=context_rows,
            held_equities=held_equities,
            held_option_underlyings=held_option_underlyings,
        )

    def run_cycle(
        self,
        *,
        execute_orders: bool = True,
        lookback_hours_override: int | None = None,
    ) -> dict[str, Any]:
        snapshot = self.broker.get_portfolio_snapshot()
        signals, collection_metadata, research_items = self._collect_signals(lookback_hours_override=lookback_hours_override)
        signals_by_symbol = {signal.symbol: signal for signal in signals}
        account_equity = self._estimate_account_equity(snapshot, signals_by_symbol)
        llm_plan = self._generate_llm_plan(
            snapshot=snapshot,
            signals=signals,
            research_items=research_items,
        )
        orders, llm_plan_used = self._build_orders(snapshot, signals, llm_plan=llm_plan) if execute_orders else ([], False)
        executed = [self.broker.place_order(order) for order in orders] if execute_orders else []
        decision_metadata = self._build_decision_metadata(
            signals=signals,
            orders=orders,
            account_equity=account_equity,
            execute_orders=execute_orders,
            llm_plan=llm_plan,
            llm_plan_used=llm_plan_used,
        )

        top_signals = sorted(signals, key=lambda s: s.score, reverse=True)[:5]
        logging.info(
            "Cycle complete. live=%s signals=%d orders=%d cash=%.2f",
            self.config.live_trading,
            len(signals),
            len(orders),
            snapshot.cash,
        )

        return {
            "live_trading": self.config.live_trading,
            "execute_orders": execute_orders,
            "lookback_hours_override": lookback_hours_override,
            "cash": snapshot.cash,
            "account_equity": account_equity,
            "equity_positions": snapshot.equity_positions,
            "option_positions": snapshot.option_positions,
            "signals": [asdict(s) for s in top_signals],
            "signal_map": {signal.symbol: asdict(signal) for signal in signals},
            "orders": [asdict(o) for o in orders],
            "execution": executed,
            "collection_metadata": collection_metadata,
            "research_items": research_items,
            "decision_metadata": decision_metadata,
            "feature_penalties": (
                dict(self.decision_learning.feature_penalties)
                if self.decision_learning is not None
                else {}
            ),
            "source_bias": (
                dict(self.decision_learning.source_bias)
                if self.decision_learning is not None
                else {}
            ),
            "llm_plan": (
                {
                    "equity_buy_symbols": llm_plan.equity_buy_symbols,
                    "option_buy_symbols": llm_plan.option_buy_symbols,
                    "exit_symbols": llm_plan.exit_symbols,
                    "confidence": llm_plan.confidence,
                    "summary": llm_plan.summary,
                    "rationale_by_symbol": llm_plan.rationale_by_symbol,
                }
                if llm_plan is not None
                else {}
            ),
            "llm_plan_used": llm_plan_used,
        }

    def _collect_signals(
        self,
        *,
        lookback_hours_override: int | None = None,
    ) -> tuple[list[Signal], dict[str, Any], list[dict[str, Any]]]:
        signals: list[Signal] = []
        research_feed_items: list[dict[str, Any]] = []
        symbols_with_market_data = 0
        symbols_with_research = 0
        research_items_total = 0
        research_items_by_source: dict[str, int] = {}
        research_items_by_symbol: dict[str, int] = {}
        historical_pattern_feedback_events = 0
        decision_window_lookback = self._decision_window_lookback(
            self.config.decision_research_lookback_hours,
            lookback_hours_override,
        )
        macro_assessment = self.macro_model.evaluate(
            lookback_hours_override=max(decision_window_lookback, self.config.macro_news_lookback_hours),
        )

        for symbol, news_query in self.theme_map.items():
            try:
                price = self.broker.get_last_price(symbol)
                closes = self.broker.get_history(symbol, days=90)
            except Exception as exc:
                logging.warning("Market data failed for %s: %s", symbol, exc)
                continue

            if price is None and closes:
                price = closes[-1]

            if price is None:
                continue

            symbols_with_market_data += 1

            if (
                self.historical_research_memory is not None
                and self.config.enable_historical_research_feedback_learning
            ):
                adjustment = self.historical_research_memory.apply_price_feedback(
                    symbol,
                    price,
                    self.config.historical_research_feedback_strength,
                )
                if adjustment != 0:
                    historical_pattern_feedback_events += 1
                    logging.debug(
                        "Applied historical research feedback update for %s: %.4f",
                        symbol,
                        adjustment,
                    )

            if self.decision_learning is not None:
                resolved = self.decision_learning.maybe_resolve_call(symbol=symbol, current_price=price)
                if resolved is not None and resolved.get("outcome") == "bad_call":
                    logging.info(
                        "Resolved bad call for %s return=%.4f tags=%s",
                        symbol,
                        float(resolved.get("realized_return", 0.0)),
                        resolved.get("why_bad"),
                    )

            if self.ai_interpreter.enabled and self.config.enable_ai_feedback_learning:
                adjustment = self.long_term_memory.apply_price_feedback(
                    symbol,
                    price,
                    self.config.ai_feedback_strength,
                )
                if adjustment != 0:
                    logging.debug("Applied AI feedback update for %s: %.4f", symbol, adjustment)

            try:
                research_items = collect_research_items(
                    symbol,
                    news_query,
                    news_lookback_hours=max(decision_window_lookback, self.config.news_lookback_hours),
                    sec_lookback_hours=max(decision_window_lookback, self.config.sec_filings_lookback_hours),
                    earnings_lookback_hours=max(
                        decision_window_lookback,
                        self.config.earnings_transcript_lookback_hours,
                    ),
                    social_lookback_hours=max(decision_window_lookback, self.config.social_feed_lookback_hours),
                    analyst_lookback_hours=max(decision_window_lookback, self.config.analyst_rating_lookback_hours),
                    max_items_per_source=self.config.research_items_per_source,
                    total_items_cap=self.config.research_total_items_cap,
                    timeout_seconds=self.config.request_timeout_seconds,
                    include_full_article_text=self.config.enable_full_article_text,
                    article_text_max_chars=self.config.article_text_max_chars,
                    enable_sec_filings=self.config.enable_sec_filings,
                    sec_user_agent=self.config.sec_user_agent,
                    sec_forms=self.config.sec_forms,
                    enable_earnings_transcripts=self.config.enable_earnings_transcripts,
                    fmp_api_key=self.config.fmp_api_key,
                    earnings_transcript_max_chars=self.config.earnings_transcript_max_chars,
                    enable_social_feeds=self.config.enable_social_feeds,
                    social_feed_rss_urls=self.config.social_feed_rss_urls,
                    trusted_social_accounts=self.config.trusted_social_accounts,
                    enable_analyst_ratings=self.config.enable_analyst_ratings,
                    finnhub_api_key=self.config.finnhub_api_key,
                )
            except Exception as exc:
                logging.warning("Research lookup failed for %s: %s", symbol, exc)
                research_items = []

            for item in research_items:
                if len(research_feed_items) >= self.config.dashboard_research_items_per_cycle:
                    break
                summary = self._compact_research_summary(
                    title=item.title,
                    description=item.description,
                    content=item.content,
                )
                research_feed_items.append(
                    {
                        "symbol": symbol,
                        "source_type": (item.source_type or "unknown").strip().lower() or "unknown",
                        "source": item.source,
                        "title": item.title,
                        "description": item.description,
                        "summary": summary,
                        "key_points": self._compact_key_points(
                            title=item.title,
                            description=item.description,
                            content=item.content,
                        ),
                        "link": item.link,
                        "published_at": (item.published_at.isoformat() if item.published_at is not None else ""),
                    }
                )

            research_items_by_symbol[symbol] = len(research_items)
            if research_items:
                symbols_with_research += 1
            research_items_total += len(research_items)
            for item in research_items:
                source_type = (item.source_type or "unknown").strip().lower() or "unknown"
                research_items_by_source[source_type] = research_items_by_source.get(source_type, 0) + 1

            source_types = sorted(
                {(item.source_type or "unknown").strip().lower() or "unknown" for item in research_items}
            )
            source_multipliers: dict[str, float] = {}
            if (
                self.decision_learning is not None
                and self.config.enable_source_priority_learning
                and source_types
            ):
                source_multipliers = self.decision_learning.source_multipliers_for(source_types)

            news_score, sentiment_by_source, count_by_source = source_weighted_sentiment(
                research_items,
                source_multipliers=(
                    source_multipliers if self.config.enable_source_priority_learning else None
                ),
            )
            historical_news_score = news_score
            blended_news_score = news_score
            if self.historical_research_memory is not None:
                historical_news_score = self.historical_research_memory.update(symbol, news_score)
                self.historical_research_memory.record_prediction(symbol, news_score, price)
                blended_news_score = self._blend_news_with_history(
                    current_news_score=news_score,
                    historical_news_score=historical_news_score,
                    history_weight=self.config.historical_research_weight,
                )
            source_profile: dict[str, dict[str, float | int]] = {}
            for source_type, sentiment in sentiment_by_source.items():
                source_profile[source_type] = {
                    "sentiment": sentiment,
                    "count": int(count_by_source.get(source_type, 0)),
                    "multiplier": float(source_multipliers.get(source_type, 1.0)),
                }

            if (
                self.decision_learning is not None
                and self.config.enable_source_priority_learning
                and self.config.enable_source_market_reaction_learning
            ):
                self.decision_learning.update_from_market_reaction(
                    symbol=symbol,
                    current_price=price,
                    source_profile=source_profile,
                )

            ai_short_term_score = 0.0
            ai_long_term_score = 0.0
            ai_confidence = 0.0
            if self.ai_interpreter.enabled:
                if research_items:
                    outlook = self.ai_interpreter.analyze(symbol, news_query, research_items)
                    ai_confidence = outlook.confidence
                    ai_short_term_score = outlook.short_term * ai_confidence
                    fresh_long_term = outlook.long_term * ai_confidence
                    ai_long_term_score = self.long_term_memory.update(
                        symbol,
                        fresh_long_term,
                    )
                    self.long_term_memory.record_prediction(symbol, fresh_long_term, price)
                else:
                    ai_long_term_score = self.long_term_memory.get(symbol)

            signal = compute_signal_with_ai(
                symbol,
                price,
                closes,
                blended_news_score,
                ai_short_term_score=ai_short_term_score,
                ai_long_term_score=ai_long_term_score,
                ai_confidence=ai_confidence,
                ai_short_term_weight=self.config.ai_short_term_weight,
                ai_long_term_weight=self.config.ai_long_term_weight,
            )
            if signal is not None:
                signal = Signal(
                    symbol=signal.symbol,
                    price=signal.price,
                    momentum_20d=signal.momentum_20d,
                    momentum_5d=signal.momentum_5d,
                    trend_20d=signal.trend_20d,
                    volatility_20d=signal.volatility_20d,
                    news_score=signal.news_score,
                    score=signal.score,
                    current_news_score=news_score,
                    historical_news_score=historical_news_score,
                    ai_short_term_score=signal.ai_short_term_score,
                    ai_long_term_score=signal.ai_long_term_score,
                    ai_confidence=signal.ai_confidence,
                    macro_score=signal.macro_score,
                )

                if macro_assessment.enabled:
                    macro_component = self.config.macro_model_weight * macro_assessment.score
                    signal = Signal(
                        symbol=signal.symbol,
                        price=signal.price,
                        momentum_20d=signal.momentum_20d,
                        momentum_5d=signal.momentum_5d,
                        trend_20d=signal.trend_20d,
                        volatility_20d=signal.volatility_20d,
                        news_score=signal.news_score,
                        score=signal.score + macro_component,
                        current_news_score=signal.current_news_score,
                        historical_news_score=signal.historical_news_score,
                        ai_short_term_score=signal.ai_short_term_score,
                        ai_long_term_score=signal.ai_long_term_score,
                        ai_confidence=signal.ai_confidence,
                        macro_score=macro_assessment.score,
                    )

                feature_profile = signal_feature_profile(
                    signal,
                    ai_short_term_weight=self.config.ai_short_term_weight,
                    ai_long_term_weight=self.config.ai_long_term_weight,
                    macro_weight=self.config.macro_model_weight,
                )

                if self.decision_learning is not None:
                    learned_adjustment = self.decision_learning.adjustment_for(feature_profile)
                    if learned_adjustment != 0:
                        signal = Signal(
                            symbol=signal.symbol,
                            price=signal.price,
                            momentum_20d=signal.momentum_20d,
                            momentum_5d=signal.momentum_5d,
                            trend_20d=signal.trend_20d,
                            volatility_20d=signal.volatility_20d,
                            news_score=signal.news_score,
                            score=signal.score + learned_adjustment,
                            current_news_score=signal.current_news_score,
                            historical_news_score=signal.historical_news_score,
                            ai_short_term_score=signal.ai_short_term_score,
                            ai_long_term_score=signal.ai_long_term_score,
                            ai_confidence=signal.ai_confidence,
                            macro_score=signal.macro_score,
                        )

                    self.decision_learning.maybe_record_call(
                        signal=signal,
                        feature_profile=feature_profile,
                        source_profile=source_profile,
                        entry_threshold=self.config.min_signal_to_enter,
                        option_threshold=self.config.option_signal_threshold,
                    )

                signals.append(signal)

        signals.sort(key=lambda item: item.score, reverse=True)
        metadata = {
            "symbols_analyzed": len(self.theme_map),
            "symbols_with_market_data": symbols_with_market_data,
            "symbols_with_research": symbols_with_research,
            "research_items_total": research_items_total,
            "research_items_by_source": research_items_by_source,
            "research_items_by_symbol": research_items_by_symbol,
            "decision_research_lookback_hours_effective": decision_window_lookback,
            "historical_research_memory_enabled": self.historical_research_memory is not None,
            "historical_research_weight": self.config.historical_research_weight,
            "historical_research_feedback_learning_enabled": (
                self.historical_research_memory is not None
                and self.config.enable_historical_research_feedback_learning
            ),
            "historical_pattern_feedback_events": historical_pattern_feedback_events,
            "macro_policy_model": asdict(macro_assessment),
        }
        return signals, metadata, research_feed_items

    def _build_orders(
        self,
        snapshot: PortfolioSnapshot,
        signals: list[Signal],
        *,
        llm_plan: LLMDecisionPlan | None = None,
    ) -> tuple[list[TradeOrder], bool]:
        if not signals:
            return [], False

        signals_by_symbol = {signal.symbol: signal for signal in signals}
        account_equity = self._estimate_account_equity(snapshot, signals_by_symbol)

        if (
            llm_plan is not None
            and self.config.enable_llm_first_decisioning
            and llm_plan.confidence >= self.config.llm_first_min_confidence
        ):
            llm_orders = self._build_orders_from_llm_plan(
                snapshot=snapshot,
                signals=signals,
                signals_by_symbol=signals_by_symbol,
                account_equity=account_equity,
                llm_plan=llm_plan,
            )
            if llm_orders:
                if len(llm_orders) > self.config.max_orders_per_cycle:
                    llm_orders = llm_orders[: self.config.max_orders_per_cycle]
                return llm_orders, True
            logging.info(
                "LLM plan generated no actionable orders; falling back to supporting rules/signal engine."
            )

        equity_orders, est_cash = self._build_equity_orders(snapshot, signals, signals_by_symbol, account_equity)
        option_orders = self._build_option_orders(snapshot, signals, signals_by_symbol, account_equity, est_cash)

        combined = equity_orders + option_orders
        if len(combined) > self.config.max_orders_per_cycle:
            combined = combined[: self.config.max_orders_per_cycle]
        return combined, False

    def _build_orders_from_llm_plan(
        self,
        *,
        snapshot: PortfolioSnapshot,
        signals: list[Signal],
        signals_by_symbol: dict[str, Signal],
        account_equity: float,
        llm_plan: LLMDecisionPlan,
    ) -> list[TradeOrder]:
        forced_exit_symbols = {symbol.upper() for symbol in llm_plan.exit_symbols}

        equity_candidates: list[Signal] = []
        for symbol in llm_plan.equity_buy_symbols:
            signal = signals_by_symbol.get(symbol)
            if signal is None:
                continue
            if not self._signal_supports_llm_entry(signal):
                continue
            equity_candidates.append(signal)

        option_candidates: list[Signal] = []
        for symbol in llm_plan.option_buy_symbols:
            signal = signals_by_symbol.get(symbol)
            if signal is None:
                continue
            if not self._signal_supports_llm_entry(signal):
                continue
            option_candidates.append(signal)

        equity_orders, est_cash = self._build_equity_orders(
            snapshot,
            signals,
            signals_by_symbol,
            account_equity,
            candidate_signals_override=equity_candidates,
            forced_exit_symbols=forced_exit_symbols,
        )
        option_orders = self._build_option_orders(
            snapshot,
            signals,
            signals_by_symbol,
            account_equity,
            est_cash,
            candidate_signals_override=option_candidates,
            forced_exit_symbols=forced_exit_symbols,
        )

        combined = equity_orders + option_orders
        if len(combined) > self.config.max_orders_per_cycle:
            combined = combined[: self.config.max_orders_per_cycle]
        return combined

    def _estimate_account_equity(
        self,
        snapshot: PortfolioSnapshot,
        signals_by_symbol: dict[str, Signal],
    ) -> float:
        equity_value = snapshot.cash
        for symbol, quantity in snapshot.equity_positions.items():
            if quantity == 0:
                continue
            signal = signals_by_symbol.get(symbol)
            if signal is not None:
                equity_value += quantity * signal.price
                continue

            try:
                latest = self.broker.get_last_price(symbol)
            except Exception:
                latest = None
            if latest is not None:
                equity_value += quantity * latest

        return max(equity_value, self.config.starting_capital)

    def _build_equity_orders(
        self,
        snapshot: PortfolioSnapshot,
        signals: list[Signal],
        signals_by_symbol: dict[str, Signal],
        account_equity: float,
        *,
        candidate_signals_override: list[Signal] | None = None,
        forced_exit_symbols: set[str] | None = None,
    ) -> tuple[list[TradeOrder], float]:
        if candidate_signals_override is None:
            candidate_signals = [signal for signal in signals if signal.score >= self.config.min_signal_to_enter]
        else:
            candidate_signals = []
            seen_symbols: set[str] = set()
            for signal in candidate_signals_override:
                symbol = signal.symbol.upper()
                if symbol in seen_symbols:
                    continue
                if symbol not in signals_by_symbol:
                    continue
                seen_symbols.add(symbol)
                candidate_signals.append(signal)
        candidate_signals = candidate_signals[: self.config.max_equity_positions]

        orders: list[TradeOrder] = []
        estimated_cash = snapshot.cash
        forced_exits = {symbol.upper() for symbol in (forced_exit_symbols or set())}

        target_qty: dict[str, int] = {}
        if candidate_signals:
            equity_budget = account_equity * self.config.equity_capital_fraction
            per_position_budget = min(
                account_equity * self.config.max_position_fraction,
                equity_budget / len(candidate_signals),
            )
            for signal in candidate_signals:
                qty = int(per_position_budget // signal.price)
                target_qty[signal.symbol] = max(0, qty)

        # Exits first to free cash and cut weak names.
        for symbol, quantity in snapshot.equity_positions.items():
            if quantity <= 0:
                continue

            if symbol.upper() in forced_exits:
                orders.append(
                    TradeOrder(
                        asset_type="EQUITY",
                        symbol=symbol,
                        instruction="SELL",
                        quantity=quantity,
                        limit_price=None,
                        reason="llm_forced_exit",
                    )
                )
                signal = signals_by_symbol.get(symbol)
                if signal is not None:
                    estimated_cash += quantity * signal.price
                continue

            signal = signals_by_symbol.get(symbol)
            desired = target_qty.get(symbol)
            if desired is not None:
                if quantity > desired:
                    to_sell = quantity - desired
                    if to_sell > 0:
                        orders.append(
                            TradeOrder(
                                asset_type="EQUITY",
                                symbol=symbol,
                                instruction="SELL",
                                quantity=to_sell,
                                limit_price=None,
                                reason="rebalance_down",
                            )
                        )
                        estimated_cash += to_sell * signal.price
                continue

            if signal is None or signal.score <= self.config.signal_to_exit:
                orders.append(
                    TradeOrder(
                        asset_type="EQUITY",
                        symbol=symbol,
                        instruction="SELL",
                        quantity=quantity,
                        limit_price=None,
                        reason="signal_exit",
                    )
                )
                if signal is not None:
                    estimated_cash += quantity * signal.price

        # Entries and add-ons.
        for signal in candidate_signals:
            desired = target_qty.get(signal.symbol, 0)
            held = snapshot.equity_positions.get(signal.symbol, 0)
            to_buy = desired - held
            if to_buy <= 0:
                continue

            notional = to_buy * signal.price
            if notional < self.config.min_order_notional:
                continue
            if notional > estimated_cash:
                continue

            orders.append(
                TradeOrder(
                    asset_type="EQUITY",
                    symbol=signal.symbol,
                    instruction="BUY",
                    quantity=to_buy,
                    limit_price=round(signal.price * 1.0025, 2),
                    reason=f"signal_entry_{signal.score:.4f}",
                )
            )
            estimated_cash -= notional

        return orders, estimated_cash

    def _build_option_orders(
        self,
        snapshot: PortfolioSnapshot,
        signals: list[Signal],
        signals_by_symbol: dict[str, Signal],
        account_equity: float,
        estimated_cash: float,
        *,
        candidate_signals_override: list[Signal] | None = None,
        forced_exit_symbols: set[str] | None = None,
    ) -> list[TradeOrder]:
        if not self.config.enable_options or self.config.max_option_contracts <= 0:
            return []

        orders: list[TradeOrder] = []
        forced_exits = {symbol.upper() for symbol in (forced_exit_symbols or set())}

        # Close options tied to weak underlyings.
        for option_symbol, quantity in snapshot.option_positions.items():
            if quantity <= 0:
                continue
            underlying = option_underlying(option_symbol)
            signal = signals_by_symbol.get(underlying)
            if underlying.upper() in forced_exits:
                orders.append(
                    TradeOrder(
                        asset_type="OPTION",
                        symbol=option_symbol,
                        instruction="SELL_TO_CLOSE",
                        quantity=quantity,
                        limit_price=None,
                        reason="llm_forced_exit",
                    )
                )
                continue
            if signal is not None and signal.score <= self.config.signal_to_exit:
                orders.append(
                    TradeOrder(
                        asset_type="OPTION",
                        symbol=option_symbol,
                        instruction="SELL_TO_CLOSE",
                        quantity=quantity,
                        limit_price=None,
                        reason="underlying_signal_exit",
                    )
                )

        open_contracts = sum(max(0, qty) for qty in snapshot.option_positions.values())
        remaining_slots = self.config.max_option_contracts - open_contracts
        if remaining_slots <= 0:
            return orders

        max_option_budget = account_equity * self.config.option_capital_fraction
        max_option_budget = min(max_option_budget, estimated_cash)
        if max_option_budget < self.config.min_order_notional:
            return orders

        budget_left = max_option_budget
        cash_left = estimated_cash
        option_underlyings_held = {
            option_underlying(symbol)
            for symbol, quantity in snapshot.option_positions.items()
            if quantity > 0
        }
        candidate_signals: list[Signal]
        if candidate_signals_override is None:
            candidate_signals = signals
        else:
            candidate_signals = []
            seen_symbols: set[str] = set()
            for signal in candidate_signals_override:
                symbol = signal.symbol.upper()
                if symbol in seen_symbols:
                    continue
                if symbol not in signals_by_symbol:
                    continue
                seen_symbols.add(symbol)
                candidate_signals.append(signal)

        for signal in candidate_signals:
            if remaining_slots <= 0:
                break
            if signal.symbol.upper() in forced_exits:
                continue
            if candidate_signals_override is None and signal.score < self.config.option_signal_threshold:
                break
            if signal.symbol in option_underlyings_held:
                continue

            per_contract_budget = min(budget_left, cash_left)
            if per_contract_budget < self.config.min_order_notional:
                break

            try:
                chain = self.broker.get_option_chain(signal.symbol)
            except Exception as exc:
                logging.warning("Option chain fetch failed for %s: %s", signal.symbol, exc)
                continue

            contract = choose_bullish_call(
                chain,
                max_premium_dollars=per_contract_budget,
                min_dte=self.config.option_min_dte,
                max_dte=self.config.option_max_dte,
                target_delta=self.config.option_target_delta,
            )
            if contract is None:
                continue

            if contract.premium_per_contract > per_contract_budget:
                continue

            limit_basis = contract.ask if contract.ask > 0 else contract.mark
            limit_price = round(limit_basis, 2) if limit_basis > 0 else None

            orders.append(
                TradeOrder(
                    asset_type="OPTION",
                    symbol=contract.symbol,
                    instruction="BUY_TO_OPEN",
                    quantity=1,
                    limit_price=limit_price,
                    reason=(
                        f"option_overlay_{signal.symbol}_score_{signal.score:.4f}_"
                        f"dte_{contract.dte}_delta_{contract.delta}"
                    ),
                )
            )
            remaining_slots -= 1
            budget_left -= contract.premium_per_contract
            cash_left -= contract.premium_per_contract
            option_underlyings_held.add(signal.symbol)

        return orders
