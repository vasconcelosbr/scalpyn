"""Celery Task — evaluate signals, check blocks, apply risk, execute trades."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text, select

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _resolve_signal_score(
    symbol: str,
    indicators: dict,
    legacy_alpha_score: float,
    selector=None,
) -> float | None:
    """Phase 3 score-resolution for the per-candidate signal loop.

    Returns the score to use, or ``None`` when the candidate must be
    skipped because the robust engine declined to produce a value.
    The legacy ``alpha_score`` from ``alpha_scores`` is **never**
    substituted outside ``LEGACY_PIPELINE_ROLLBACK`` — that's the
    explicit Phase 3 contract enforced here.

    The ``selector`` argument is the ``select_authoritative_score``
    callable; it's a kwarg so unit tests can drive the three
    contract branches (robust value / robust sentinel / legacy under
    rollback) without touching the real engine.
    """
    if selector is None:
        from ..services.robust_indicators import select_authoritative_score
        selector = select_authoritative_score

    sel = selector(
        symbol,
        indicators,
        legacy_score=legacy_alpha_score,
        flow_source_hint=indicators.get("taker_source") if indicators else None,
    )

    if sel.engine_tag == "robust":
        if sel.score is None:
            # Phase 3 sentinel — robust declined to score this symbol.
            # Never substitute legacy. The caller skips the candidate.
            logger.debug(
                "[evaluate_signals] skip %s — robust sentinel (%s)",
                symbol, sel.fallback_reason,
            )
            return None
        return float(sel.score)

    # engine_tag == "legacy" — only reachable while
    # LEGACY_PIPELINE_ROLLBACK is active. Use the legacy score as the
    # explicit emergency fallback the operator requested.
    if sel.score is None:
        return None
    return float(sel.score)


async def _evaluate_async():
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..services.signal_engine import SignalEngine
    from ..services.block_engine import BlockEngine
    from ..services.risk_engine import RiskEngine
    from ..services.execution_engine import execution_engine
    from ..services.analytics_service import analytics_service
    from ..services.notification_service import notification_service
    from ..services.config_service import config_service
    from ..models.user import User
    from ..models.pool import Pool

    logger.info("Starting signal evaluation...")
    signals_found = 0

    async with AsyncSessionLocal() as db:
        # Get all active users
        users_result = await db.execute(select(User).where(User.is_active == True))
        users = users_result.scalars().all()

        for user in users:
            try:
                # Get user configs
                signal_config = await config_service.get_config(db, "signal", user.id)
                block_config = await config_service.get_config(db, "block", user.id)
                risk_config = await config_service.get_config(db, "risk", user.id)

                if not signal_config or not risk_config:
                    continue

                signal_engine = SignalEngine(signal_config)
                block_engine = BlockEngine(block_config) if block_config else None
                risk_engine = RiskEngine(risk_config)

                # Get daily summary for circuit breaker data
                daily = await analytics_service.get_daily_summary(db, user.id)

                # Robust Indicators Phase 3 — candidate selection.
                # Outside ``LEGACY_PIPELINE_ROLLBACK`` the candidate
                # query must NOT prefilter on the legacy
                # ``alpha_scores.score`` column — that would let
                # legacy values decide which symbols even reach the
                # robust selector, defeating the Phase 3 contract.
                # We pull every recent-indicator symbol and let
                # ``_resolve_signal_score`` (robust authoritative) plus
                # the SignalEngine's own thresholds gate inclusion.
                # Only when the operator has explicitly armed the
                # rollback do we restore the legacy score prefilter.
                from ..services.robust_indicators import is_legacy_rollback_active
                rollback_on = bool(is_legacy_rollback_active())

                if rollback_on:
                    ranked = await db.execute(text("""
                        SELECT DISTINCT ON (a.symbol)
                            a.symbol, a.score, i.indicators_json
                        FROM alpha_scores a
                        JOIN indicators i ON a.symbol = i.symbol
                        WHERE a.time > now() - interval '2 hours'
                          AND i.time > now() - interval '2 hours'
                          AND a.score >= 60
                        ORDER BY a.symbol, a.time DESC
                    """))
                else:
                    # Robust authority: query indicators directly. We
                    # still LEFT JOIN ``alpha_scores`` so the row shape
                    # is unchanged for downstream code, but the legacy
                    # score is purely informational (and is in fact
                    # ignored by ``_resolve_signal_score`` outside
                    # rollback). NULL legacy scores are surfaced as
                    # 0.0 so the helper has a numeric type.
                    ranked = await db.execute(text("""
                        SELECT DISTINCT ON (i.symbol)
                            i.symbol,
                            COALESCE(a.score, 0.0) AS score,
                            i.indicators_json
                        FROM indicators i
                        LEFT JOIN alpha_scores a
                          ON a.symbol = i.symbol
                         AND a.time > now() - interval '2 hours'
                        WHERE i.time > now() - interval '2 hours'
                        ORDER BY i.symbol, i.time DESC, a.time DESC
                    """))
                candidates = ranked.fetchall()

                # Robust Indicators Phase 3 — authoritative score read.
                # ``_resolve_signal_score`` enforces the Phase 3
                # contract: outside ``LEGACY_PIPELINE_ROLLBACK`` the
                # legacy ``alpha_scores`` value is NEVER substituted —
                # a robust-tagged sentinel (engine declined to score)
                # returns ``None`` and we skip the candidate.
                for candidate in candidates:
                    symbol = candidate.symbol
                    legacy_alpha = float(candidate.score)
                    indicators = candidate.indicators_json or {}
                    current_price = indicators.get("close", 0)

                    if current_price <= 0:
                        continue

                    resolved = _resolve_signal_score(
                        symbol, indicators, legacy_alpha,
                    )
                    if resolved is None:
                        continue
                    alpha_score = resolved

                    # 1. Evaluate signal
                    signal_result = signal_engine.evaluate(indicators, alpha_score)
                    if not signal_result.get("signal"):
                        continue

                    # 2. Check blocks
                    if block_engine:
                        block_result = block_engine.evaluate(indicators)
                        if block_result.get("blocked"):
                            logger.info(f"Signal for {symbol} blocked: {block_result.get('triggered_blocks')}")
                            continue

                    # 3. Risk evaluation
                    risk_result = risk_engine.evaluate_trade(
                        symbol=symbol,
                        direction=signal_result.get("direction", "long"),
                        current_price=current_price,
                        indicators=indicators,
                        available_capital=100000,  # Would come from user's actual capital
                        open_positions=daily.get("open_positions", 0),
                        daily_pnl=daily.get("total_pnl", 0),
                        consecutive_losses=daily.get("consecutive_losses", 0),
                    )

                    if not risk_result.get("approved"):
                        logger.info(f"Trade for {symbol} rejected by risk: {risk_result.get('rejection_reason')}")
                        continue

                    signals_found += 1

                    # 4. Get user's pool (use first active pool or None for global)
                    pools_result = await db.execute(
                        select(Pool).where(Pool.user_id == user.id, Pool.is_active == True).limit(1)
                    )
                    pool = pools_result.scalars().first()
                    pool_mode = pool.mode if pool else "paper"

                    # 5. Execute trade
                    trade_result = await execution_engine.execute_trade(
                        db=db,
                        user_id=user.id,
                        pool_id=pool.id if pool else None,
                        symbol=symbol,
                        direction=signal_result["direction"],
                        market_type="spot",
                        risk_params=risk_result,
                        indicators=indicators,
                        alpha_score=alpha_score,
                        paper_mode=(pool_mode == "paper"),
                    )

                    if trade_result.get("success"):
                        logger.info(f"Trade executed: {symbol} {signal_result['direction']} for user {user.id}")

                        # 6. Send notification
                        await notification_service.send_trade_alert(
                            db, user.id, "buy",
                            {"symbol": symbol, "price": current_price, "score": alpha_score}
                        )

            except Exception as e:
                logger.exception(f"Error evaluating signals for user {user.id}: {e}")
                continue

        # Also check exit conditions for open positions
        await _check_exits(db)

    logger.info(f"Signal evaluation complete: {signals_found} signals generated")
    return signals_found


async def _check_exits(db):
    """Check TP/SL for all open positions."""
    from ..services.risk_engine import RiskEngine
    from ..services.execution_engine import execution_engine
    from ..services.config_service import config_service
    from ..services.notification_service import notification_service
    from ..models.trade import Trade

    open_trades = await db.execute(select(Trade).where(Trade.status == "open"))
    trades = open_trades.scalars().all()

    for trade in trades:
        try:
            # Get current price from metadata
            price_result = await db.execute(text(
                "SELECT price FROM market_metadata WHERE symbol = :symbol"
            ), {"symbol": trade.symbol})
            price_row = price_result.fetchone()
            if not price_row:
                continue

            current_price = float(price_row.price)

            risk_config = await config_service.get_config(db, "risk", trade.user_id)
            if not risk_config:
                continue

            risk_engine = RiskEngine(risk_config)
            exit_result = risk_engine.check_exit_conditions(
                trade={
                    "entry_price": float(trade.entry_price),
                    "direction": trade.direction,
                    "take_profit_price": float(trade.take_profit_price) if trade.take_profit_price else None,
                    "stop_loss_price": float(trade.stop_loss_price) if trade.stop_loss_price else None,
                },
                current_price=current_price,
            )

            if exit_result.get("should_exit"):
                result = await execution_engine.close_trade(
                    db=db,
                    trade_id=trade.id,
                    exit_price=current_price,
                    exit_reason=exit_result["exit_reason"],
                )
                if result.get("success"):
                    event_type = exit_result.get("exit_type", "sell")
                    await notification_service.send_trade_alert(
                        db, trade.user_id, event_type,
                        {"symbol": trade.symbol, "price": current_price, "profit_loss": result.get("profit_loss", 0)}
                    )

        except Exception as e:
            logger.warning(f"Error checking exit for trade {trade.id}: {e}")


@celery_app.task(name="app.tasks.evaluate_signals.evaluate")
def evaluate():
    count = _run_async(_evaluate_async())
    return f"Evaluated signals: {count} trades generated"
