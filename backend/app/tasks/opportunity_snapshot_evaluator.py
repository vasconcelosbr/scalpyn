"""
Opportunity Snapshot Evaluator — populates future_outcome fields.

For each opportunity_snapshot where future_evaluated_at IS NULL:

  APPROVED path (profiles_approved not empty):
    Looks up the matching shadow_trade by (user_id, symbol, profile_id,
    created_at window) and copies its outcome once closed.

  REJECTED path (counterfactual — profiles_approved empty):
    Simulates TP/SL detection using the local ohlcv table with configurable
    reference TP/SL percentages, tracking MAE/MFE across the window.

Knobs (env)
-----------
OPP_EVAL_BATCH_SIZE          — snapshots per run (default 200)
OPP_EVAL_INTERVAL_S          — beat cadence in seconds (default 1800 = 30 min)
OPP_EVAL_LOOKBACK_DAYS       — how far back to evaluate (default 90)
OPP_EVAL_DEFAULT_TP_PCT      — reference TP % for counterfactual (default 1.5)
OPP_EVAL_DEFAULT_SL_PCT      — reference SL % for counterfactual (default 1.0)
OPP_EVAL_MAX_HOLDING_HOURS   — simulation horizon for counterfactual (default 48)
OPP_EVAL_APPROVAL_WINDOW_MIN — minutes around snapshot.created_at to search for
                               matching shadow_trade (default 30)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text

from .celery_app import celery_app

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


BATCH_SIZE = _env_int("OPP_EVAL_BATCH_SIZE", 200)
LOOKBACK_DAYS = _env_int("OPP_EVAL_LOOKBACK_DAYS", 90)
DEFAULT_TP_PCT = _env_float("OPP_EVAL_DEFAULT_TP_PCT", 1.5)
DEFAULT_SL_PCT = _env_float("OPP_EVAL_DEFAULT_SL_PCT", 1.0)
MAX_HOLDING_HOURS = _env_int("OPP_EVAL_MAX_HOLDING_HOURS", 48)
APPROVAL_WINDOW_MIN = _env_int("OPP_EVAL_APPROVAL_WINDOW_MIN", 30)


# ── Async helpers ─────────────────────────────────────────────────────────────

async def _fetch_ohlcv_window(
    db,
    symbol: str,
    after_ts: datetime,
    before_ts: datetime,
    timeframe: str = "1h",
) -> List[Dict[str, Any]]:
    """OHLCV candles in (after_ts, before_ts] from the local ohlcv table."""
    res = await db.execute(
        text("""
            SELECT time, open, high, low, close
              FROM ohlcv
             WHERE symbol = :s
               AND timeframe = :tf
               AND time > :t_start
               AND time <= :t_end
             ORDER BY time ASC
        """),
        {"s": symbol, "tf": timeframe, "t_start": after_ts, "t_end": before_ts},
    )
    return [
        {
            "time": r.time,
            "open":  float(r.open)  if r.open  is not None else None,
            "high":  float(r.high)  if r.high  is not None else None,
            "low":   float(r.low)   if r.low   is not None else None,
            "close": float(r.close) if r.close is not None else None,
        }
        for r in res.fetchall()
    ]


async def _evaluate_approved(db, snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Looks up a closed shadow_trade matching this approved snapshot.

    Returns a dict of future_* fields to set, or None if evaluation should be
    deferred (trade still open or not yet created).
    """
    snap_id = snap["id"]
    user_id = snap["user_id"]
    symbol = snap["symbol"]
    snap_ts: datetime = snap["created_at"]
    profiles_approved: List[Any] = snap["profiles_approved"] or []
    created_at_cutoff = snap_ts - timedelta(days=7)

    if snap_ts.tzinfo is None:
        snap_ts = snap_ts.replace(tzinfo=timezone.utc)

    window_start = snap_ts - timedelta(minutes=10)
    window_end = snap_ts + timedelta(minutes=APPROVAL_WINDOW_MIN)

    profile_filter = ""
    params: Dict[str, Any] = {
        "uid": str(user_id),
        "sym": symbol,
        "t0": window_start,
        "t1": window_end,
    }

    if profiles_approved:
        profile_ids = [UUID(str(p)) for p in profiles_approved]
        # Use ANY with a text array cast for asyncpg compatibility
        profile_filter = "AND profile_id = ANY(CAST(:pids AS uuid[]))"
        params["pids"] = profile_ids

    res = await db.execute(
        text(f"""
            SELECT outcome, pnl_pct, mae_pct, mfe_pct, holding_seconds
              FROM shadow_trades
             WHERE user_id = :uid
               AND symbol = :sym
               AND created_at >= :t0
               AND created_at <= :t1
               {profile_filter}
             ORDER BY created_at ASC
             LIMIT 1
        """),
        params,
    )
    row = res.fetchone()

    if row is None:
        # No matching shadow_trade. If snapshot is older than 7 days → MISSED_ENTRY.
        now_utc = datetime.now(timezone.utc)
        if snap_ts < now_utc - timedelta(days=7):
            logger.debug(
                "[OppEval] snap %s approved but no shadow_trade after 7 days → MISSED_ENTRY", snap_id
            )
            return {
                "future_outcome": "MISSED_ENTRY",
                "future_pnl_pct": None,
                "future_time_to_tp_seconds": None,
                "future_time_to_sl_seconds": None,
                "future_mae_pct": None,
                "future_mfe_pct": None,
            }
        # Trade might still be PENDING/RUNNING — defer.
        return None

    outcome = row.outcome
    if outcome is None:
        # Shadow trade exists but not yet closed — defer.
        return None

    pnl_pct = float(row.pnl_pct) if row.pnl_pct is not None else None
    mae_pct = float(row.mae_pct) if row.mae_pct is not None else None
    mfe_pct = float(row.mfe_pct) if row.mfe_pct is not None else None
    holding = int(row.holding_seconds) if row.holding_seconds is not None else None

    return {
        "future_outcome": outcome,
        "future_pnl_pct": pnl_pct,
        "future_time_to_tp_seconds": holding if outcome == "TP_HIT" else None,
        "future_time_to_sl_seconds": holding if outcome == "SL_HIT" else None,
        "future_mae_pct": mae_pct,
        "future_mfe_pct": mfe_pct,
    }


async def _evaluate_rejected(db, snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Counterfactual simulation for a rejected snapshot.

    Uses the local ohlcv table to simulate TP/SL detection from the snapshot
    timestamp forward, with configurable reference TP/SL percentages.
    """
    snap_id = snap["id"]
    symbol = snap["symbol"]
    entry_price_raw = snap["price"]
    snap_ts: datetime = snap["created_at"]

    if snap_ts.tzinfo is None:
        snap_ts = snap_ts.replace(tzinfo=timezone.utc)

    if entry_price_raw is None:
        logger.debug("[OppEval] snap %s has no price — cannot simulate", snap_id)
        # Only mark permanently unevaluable if old enough.
        now_utc = datetime.now(timezone.utc)
        if snap_ts < now_utc - timedelta(days=7):
            return {
                "future_outcome": "NO_ENTRY_PRICE",
                "future_pnl_pct": None,
                "future_time_to_tp_seconds": None,
                "future_time_to_sl_seconds": None,
                "future_mae_pct": None,
                "future_mfe_pct": None,
            }
        return None

    entry_price = float(entry_price_raw)
    if entry_price <= 0:
        return {
            "future_outcome": "NO_ENTRY_PRICE",
            "future_pnl_pct": None,
            "future_time_to_tp_seconds": None,
            "future_time_to_sl_seconds": None,
            "future_mae_pct": None,
            "future_mfe_pct": None,
        }

    tp_price = entry_price * (1 + DEFAULT_TP_PCT / 100.0)
    sl_price = entry_price * (1 - DEFAULT_SL_PCT / 100.0)
    window_end = snap_ts + timedelta(hours=MAX_HOLDING_HOURS)

    candles = await _fetch_ohlcv_window(db, symbol, snap_ts, window_end, timeframe="1h")

    if not candles:
        # No OHLCV data yet. Defer if snapshot is recent; mark NO_MARKET_DATA if old.
        now_utc = datetime.now(timezone.utc)
        if snap_ts >= now_utc - timedelta(hours=MAX_HOLDING_HOURS + 4):
            return None  # too recent — data may not be in yet
        logger.debug(
            "[OppEval] snap %s: no 1h OHLCV for %s after %s → NO_MARKET_DATA",
            snap_id, symbol, snap_ts,
        )
        return {
            "future_outcome": "NO_MARKET_DATA",
            "future_pnl_pct": None,
            "future_time_to_tp_seconds": None,
            "future_time_to_sl_seconds": None,
            "future_mae_pct": None,
            "future_mfe_pct": None,
        }

    outcome: Optional[str] = None
    exit_pnl: Optional[float] = None
    time_to_tp_s: Optional[int] = None
    time_to_sl_s: Optional[int] = None
    running_min_low = entry_price
    running_max_high = entry_price

    for candle in candles:
        high = candle["high"]
        low = candle["low"]
        close = candle["close"]
        ctime: datetime = candle["time"]

        if high is None or low is None:
            continue

        if ctime.tzinfo is None:
            ctime = ctime.replace(tzinfo=timezone.utc)

        running_min_low = min(running_min_low, low)
        running_max_high = max(running_max_high, high)

        sl_hit = low <= sl_price
        tp_hit = high >= tp_price

        if sl_hit or tp_hit:
            elapsed = int((ctime - snap_ts).total_seconds())
            if sl_hit:
                # Worst-case: SL takes precedence when both hit same candle
                outcome = "SL_HIT"
                exit_pnl = (sl_price - entry_price) / entry_price * 100.0
                time_to_sl_s = max(elapsed, 0)
            else:
                outcome = "TP_HIT"
                exit_pnl = (tp_price - entry_price) / entry_price * 100.0
                time_to_tp_s = max(elapsed, 0)
            break

    if outcome is None:
        outcome = "TIMEOUT"
        last_close = candles[-1]["close"] if candles else None
        exit_pnl = (
            (last_close - entry_price) / entry_price * 100.0
            if last_close is not None and entry_price > 0
            else None
        )

    mae_pct = (running_min_low - entry_price) / entry_price * 100.0
    mfe_pct = (running_max_high - entry_price) / entry_price * 100.0

    return {
        "future_outcome": outcome,
        "future_pnl_pct": round(exit_pnl, 6) if exit_pnl is not None else None,
        "future_time_to_tp_seconds": time_to_tp_s,
        "future_time_to_sl_seconds": time_to_sl_s,
        "future_mae_pct": round(mae_pct, 6),
        "future_mfe_pct": round(mfe_pct, 6),
    }


async def _run_evaluator() -> None:
    """Main async logic: batch-evaluates pending opportunity_snapshots."""
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal

    logger.info("[OppEval] Starting evaluation pass (batch=%d lookback=%dd)", BATCH_SIZE, LOOKBACK_DAYS)

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    now_utc = datetime.now(timezone.utc)
    evaluated = 0
    deferred = 0
    errors = 0

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            text("""
                SELECT id, user_id, symbol, price, created_at,
                       profiles_approved, profiles_rejected
                  FROM opportunity_snapshots
                 WHERE future_evaluated_at IS NULL
                   AND created_at >= :cutoff
                 ORDER BY created_at ASC
                 LIMIT :batch
            """),
            {"cutoff": cutoff, "batch": BATCH_SIZE},
        )
        rows = res.fetchall()

    if not rows:
        logger.info("[OppEval] No pending snapshots — nothing to do")
        return

    logger.info("[OppEval] Processing %d snapshots", len(rows))

    for row in rows:
        snap: Dict[str, Any] = {
            "id": row.id,
            "user_id": row.user_id,
            "symbol": row.symbol,
            "price": row.price,
            "created_at": row.created_at,
            "profiles_approved": row.profiles_approved,
            "profiles_rejected": row.profiles_rejected,
        }
        snap_id = snap["id"]

        try:
            async with AsyncSessionLocal() as db:
                has_approved = bool(snap["profiles_approved"])

                if has_approved:
                    result = await _evaluate_approved(db, snap)
                else:
                    result = await _evaluate_rejected(db, snap)

                if result is None:
                    deferred += 1
                    continue

                await db.execute(
                    text("""
                        UPDATE opportunity_snapshots SET
                            future_outcome            = :outcome,
                            future_pnl_pct            = :pnl_pct,
                            future_time_to_tp_seconds = :tp_s,
                            future_time_to_sl_seconds = :sl_s,
                            future_mae_pct            = :mae,
                            future_mfe_pct            = :mfe,
                            future_evaluated_at       = NOW()
                        WHERE id = :snap_id
                    """),
                    {
                        "snap_id": str(snap_id),
                        "outcome": result["future_outcome"],
                        "pnl_pct": result["future_pnl_pct"],
                        "tp_s":    result["future_time_to_tp_seconds"],
                        "sl_s":    result["future_time_to_sl_seconds"],
                        "mae":     result["future_mae_pct"],
                        "mfe":     result["future_mfe_pct"],
                    },
                )
                await db.commit()
                evaluated += 1

        except Exception as exc:
            logger.error("[OppEval] snap %s failed: %s", snap_id, exc)
            errors += 1

    logger.info(
        "[OppEval] Done — evaluated=%d deferred=%d errors=%d",
        evaluated, deferred, errors,
    )


# ── Celery boilerplate (canonical pattern) ────────────────────────────────────

def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except BaseException as exc:
            logger.debug("[OppEval] pending-task drain: %s", exc)

        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[OppEval] engine dispose: %s", exc)

        loop.close()


@celery_app.task(name="app.tasks.opportunity_snapshot_evaluator.evaluate", bind=True)
def evaluate(self):
    """Celery entry point — evaluates pending opportunity_snapshot future_outcome fields."""
    _run_async(_run_evaluator())
