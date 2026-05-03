"""Microstructure indicator scheduler — fast indicators, 5m OHLCV + live data, 5-min cadence.

Computes VWAP, volume_spike, taker_ratio, volume_delta, volume_metrics, and
live market-data overrides (spread_pct, orderbook_depth_usdt) for every active
symbol.  Persists results to the ``indicators`` table with
``scheduler_group = 'microstructure'``.

Disable via SKIP_MICROSTRUCTURE_SCHEDULER=1.
Tune cadence via MICROSTRUCTURE_SCHEDULER_INTERVAL_SECONDS (default 300 = 5 min).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
from asyncpg.exceptions import UndefinedColumnError as _AsyncpgUndefinedColumn
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _is_scheduler_group_drift(exc: BaseException) -> bool:
    """Return True iff *exc* is the indicators.scheduler_group missing-column
    error, robust to whether asyncpg raised it directly or SQLAlchemy wrapped
    it in ProgrammingError.

    The column-name guard is checked against the asyncpg exception's OWN
    message (the Postgres "column X of relation Y does not exist" text), NOT
    against str() of the SQLAlchemy wrapper.  str(SQLAlchemy.ProgrammingError)
    includes the offending SQL statement — and our INSERT statement always
    contains "scheduler_group" by definition — so substring-matching the
    wrapper would silently swallow any asyncpg error on that INSERT (unique
    violation, FK violation, lock timeout, deadlock, …).  We therefore
    require the column name to appear in the asyncpg exception's message,
    which is only true for UndefinedColumnError("…scheduler_group…").
    """
    orig = getattr(exc, "orig", None)
    if isinstance(orig, _AsyncpgUndefinedColumn) and "scheduler_group" in str(orig):
        return True
    if isinstance(exc, _AsyncpgUndefinedColumn) and "scheduler_group" in str(exc):
        return True
    return False

DEFAULT_INTERVAL_SECONDS = 300        # 5 min
DEFAULT_CONCURRENCY = 8
DEFAULT_FIRST_RUN_DELAY_SECONDS = 15  # fire quickly after structural
DEFAULT_OHLCV_LIMIT = 100             # 100 × 5m ≈ 8 h of data
# Order-flow look-back window fed to get_order_flow_data().  Must be ≤
# TRADE_BUFFER_TTL_SECONDS (360 s) in event_handlers so the Redis buffer
# is guaranteed to cover the full window.  Tune via env var when the
# buffer TTL or cycle cadence changes.
DEFAULT_ORDER_FLOW_WINDOW_SECONDS = 300
TIMEFRAME = "5m"
SCHEDULER_GROUP = "microstructure"

_scheduler_task: Optional[asyncio.Task] = None

_first_cycle_done_event: Optional[asyncio.Event] = None

# Boot-once flag so we log the schema-drift error a single time per process,
# rather than emitting one error per symbol per cycle (~thousands per day).
# Reset only by container restart — exactly the cadence we want for an
# operator-actionable alert.  See Task #178 / migration 032.
_scheduler_group_drift_logged: bool = False


def _get_first_cycle_done_event() -> asyncio.Event:
    global _first_cycle_done_event
    if _first_cycle_done_event is None:
        _first_cycle_done_event = asyncio.Event()
    return _first_cycle_done_event


async def wait_for_first_cycle(timeout: Optional[float] = None) -> bool:
    """Block until this scheduler has completed at least one cycle."""
    event = _get_first_cycle_done_event()
    if event.is_set():
        return True
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        logger.warning("[MICRO-SCHED] Invalid int for %s=%r — using default %d",
                       name, raw, default)
        return default


# Boot-once flag for the is_approved schema-drift error so we log it a
# single time per process rather than every cycle. Mirrors the pattern
# used by ``_scheduler_group_drift_logged`` above.
_is_approved_drift_logged: bool = False


def _is_is_approved_drift(exc: BaseException) -> bool:
    """Return True iff *exc* is the pool_coins.is_approved missing-column error.

    Walks the exception chain (``__cause__`` and ``.orig`` are both used by
    SQLAlchemy/asyncpg) so the check works whether the asyncpg native
    ``UndefinedColumnError`` is one or two wrappers deep — Cloud Run sees
    it wrapped twice (SQLAlchemy ProgrammingError → asyncpg adapter
    ProgrammingError → native UndefinedColumnError).
    """
    seen: set = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _AsyncpgUndefinedColumn) and "is_approved" in str(current):
            return True
        # Final fallback: a SQLAlchemy ProgrammingError whose message text
        # explicitly mentions UndefinedColumnError + the column name.
        msg = str(current)
        if "UndefinedColumnError" in msg and "is_approved" in msg:
            return True
        current = getattr(current, "orig", None) or getattr(current, "__cause__", None)
    return False


async def _collect_symbols(db) -> List[str]:
    """Return active+approved spot symbols, degrading when migration 035 missing.

    The scheduler must keep running even when ``pool_coins.is_approved`` is
    absent (migration 035 not applied) — otherwise every cycle crashes the
    loop and the operator never sees the schema-drift alert in logs.  The
    degraded fallback returns an empty list so no symbols are processed
    until the migration is applied; the alert flag exposed via
    ``[MICRO-SCHED] SCHEMA DRIFT`` makes the issue actionable.
    """
    global _is_approved_drift_logged
    try:
        rows = (await db.execute(text("""
            SELECT DISTINCT symbol
            FROM pool_coins
            WHERE is_active = true
              AND is_approved = true
              AND symbol IS NOT NULL AND symbol <> ''
        """))).fetchall()
        return [r.symbol for r in rows]
    except Exception as exc:
        if _is_is_approved_drift(exc):
            if not _is_approved_drift_logged:
                logger.error(
                    "[MICRO-SCHED] SCHEMA DRIFT: pool_coins.is_approved column "
                    "missing — migration 035 has not been applied. The "
                    "microstructure scheduler will skip every cycle until the "
                    "column is added. Hit /api/health/schema for details."
                )
                _is_approved_drift_logged = True
            try:
                if db.in_transaction():
                    await db.rollback()
            except Exception as rb_exc:
                logger.warning(
                    "[MICRO-SCHED] rollback after is_approved drift failed: %s", rb_exc
                )
            return []
        raise


_MICRO_KEY_SOURCE_MAP: dict = {}  # populated lazily on first use


def _get_micro_key_source_map() -> dict:
    """Return source/confidence map for microstructure indicator keys.

    Built lazily to avoid a circular import at module load time.
    """
    global _MICRO_KEY_SOURCE_MAP
    if not _MICRO_KEY_SOURCE_MAP:
        from ..utils.indicator_merge import _ORDER_FLOW_KEYS, _ORDERBOOK_KEYS
        _MICRO_KEY_SOURCE_MAP = {
            **{k: ("gate_trades", 1.00) for k in _ORDER_FLOW_KEYS},
            **{k: ("gate_orderbook", 0.90) for k in _ORDERBOOK_KEYS},
        }
    return _MICRO_KEY_SOURCE_MAP


async def _persist_indicators(db, symbol: str, results: dict, when: datetime) -> None:
    if not results:
        return
    from ..utils.indicator_merge import envelop_results
    payload = json.dumps(
        envelop_results(
            results,
            default_source="gate_candles",
            default_confidence=0.85,
            key_source_map=_get_micro_key_source_map(),
        ),
        default=str,
    )
    try:
        # SAVEPOINT: isolates a constraint error so the parent transaction
        # remains healthy for _refresh_market_metadata below.
        async with db.begin_nested():
            await db.execute(text("""
                INSERT INTO indicators
                    (time, symbol, timeframe, market_type, indicators_json, scheduler_group)
                VALUES
                    (:time, :symbol, :timeframe, :market_type, :payload, :grp)
                ON CONFLICT DO NOTHING
            """), {
                "time": when,
                "symbol": symbol,
                "timeframe": TIMEFRAME,
                "market_type": "spot",
                "payload": payload,
                "grp": SCHEDULER_GROUP,
            })
    except Exception as exc:
        # Schema drift: indicators.scheduler_group column missing in the DB
        # (migration 032 not applied yet).  Log ONCE per process, force a
        # rollback on the OUTER session so subsequent statements in the same
        # _persist callback (_refresh_market_metadata) don't inherit an
        # InFailedSQLTransactionError, then return cleanly.  Without this,
        # ~30k errors/day flooded Sentry and the failed transaction state
        # blocked connections from being recycled to the pool.
        if _is_scheduler_group_drift(exc):
            global _scheduler_group_drift_logged
            if not _scheduler_group_drift_logged:
                logger.error(
                    "[MICRO-SCHED] SCHEMA DRIFT: indicators.scheduler_group column "
                    "missing — migration 032 has not been applied. Persisting will "
                    "be skipped for every symbol until the column is added. "
                    "Hit /api/health/schema for details and see "
                    "docs/runbooks/scheduler-group-drift.md."
                )
                _scheduler_group_drift_logged = True
            # Guard the rollback on the actual session state. After the
            # savepoint context manager exits with an exception, SQLAlchemy
            # may have already rolled the outer transaction back (depends on
            # whether asyncpg poisoned the parent before the savepoint
            # release).  Calling rollback() on a session that is no longer in
            # a transaction raises InvalidRequestError; gating on
            # in_transaction() avoids that and still drains the failed state
            # in the case where the outer transaction is still alive.
            try:
                if db.in_transaction():
                    await db.rollback()
            except Exception as rb_exc:
                logger.warning(
                    "[MICRO-SCHED] rollback after schema drift failed: %s", rb_exc
                )
            return
        logger.error("[MICRO-SCHED] indicators insert failed for %s: %s", symbol, exc, exc_info=True)


async def _refresh_market_metadata(db, symbol: str,
                                   spread_payload: dict, when: datetime) -> None:
    spread_pct = spread_payload.get("spread_pct")
    depth = spread_payload.get("orderbook_depth_usdt")
    if spread_pct is None and depth is None:
        return
    try:
        # SAVEPOINT: isolates a market_metadata failure from the rest of the session.
        async with db.begin_nested():
            await db.execute(text("""
                INSERT INTO market_metadata
                    (symbol, spread_pct, orderbook_depth_usdt, last_updated)
                VALUES
                    (:symbol, :spread, :depth, :updated)
                ON CONFLICT (symbol) DO UPDATE SET
                    spread_pct = COALESCE(:spread, market_metadata.spread_pct),
                    orderbook_depth_usdt = COALESCE(:depth, market_metadata.orderbook_depth_usdt),
                    last_updated = :updated
            """), {
                "symbol": symbol,
                "spread": spread_pct,
                "depth": depth,
                "updated": when,
            })
    except Exception as exc:
        logger.error("[MICRO-SCHED] market_metadata upsert failed for %s: %s", symbol, exc, exc_info=True)


async def _refresh_one_symbol(symbol: str, semaphore: asyncio.Semaphore,
                               of_window: int) -> str:
    from ..database import run_db_task
    from ..services.feature_engine import FeatureEngine
    from ..services.market_data_service import market_data_service
    from ..services.seed_service import DEFAULT_INDICATORS

    async with semaphore:
        # Fetch 5m OHLCV for volume/VWAP computation
        try:
            df = await market_data_service.fetch_ohlcv(symbol, TIMEFRAME,
                                                        limit=DEFAULT_OHLCV_LIMIT)
        except Exception as exc:
            logger.warning("[MICRO-SCHED] fetch_ohlcv failed for %s: %s", symbol, exc)
            df = None

        # Always fetch live orderbook for spread/depth/taker data
        try:
            spread_payload = await market_data_service.fetch_orderbook_metrics(symbol)
        except Exception as exc:
            logger.debug("[MICRO-SCHED] orderbook fetch failed for %s: %s", symbol, exc)
            spread_payload = {}

        results: dict = {}

        if df is not None and not df.empty:
            engine = FeatureEngine(DEFAULT_INDICATORS)
            try:
                results = engine.calculate(df, market_data=spread_payload or None,
                                           group=SCHEDULER_GROUP) or {}
            except Exception as exc:
                logger.warning("[MICRO-SCHED] FeatureEngine failed for %s: %s", symbol, exc)
                results = {}

        # Ensure spread/depth always land in the results even when OHLCV failed
        if spread_payload:
            for key in ("spread_pct", "orderbook_depth_usdt",
                        "market_data_source", "market_data_confidence",
                        "taker_buy_volume", "taker_sell_volume", "taker_ratio",
                        "volume_delta"):
                if spread_payload.get(key) is not None and key not in results:
                    results[key] = spread_payload[key]

        # ── Order flow: taker_ratio / volume_delta / buy_pressure ────────────
        # fetch_orderbook_metrics uses include_taker=False (orderbook only), so
        # real taker data never arrives via spread_payload.  Call the order-flow
        # service directly here so 5m indicators have the same real trade signal
        # as the 1h Celery path.  Window aligned to TRADE_BUFFER_TTL_SECONDS
        # (360 s) so the Redis buffer is guaranteed to cover the full lookback.
        try:
            from ..services.order_flow_service import get_order_flow_data
            from ..utils.indicator_merge import _ORDER_FLOW_KEYS
            of_data = await get_order_flow_data(
                symbol, window_seconds=of_window, market_type="spot"
            )
            for key, value in of_data.items():
                if key in _ORDER_FLOW_KEYS:
                    if value is not None or results.get(key) is None:
                        results[key] = value
                else:
                    results[key] = value
            logger.debug(
                "[MICRO-SCHED] [OF] %s taker_ratio=%s volume_delta=%s source=%s",
                symbol,
                of_data.get("taker_ratio"),
                of_data.get("volume_delta"),
                of_data.get("taker_source"),
            )
        except Exception as exc:
            logger.warning("[MICRO-SCHED] order_flow failed for %s: %s", symbol, exc)

        if not results:
            return f"{symbol}: no_data"

        now = datetime.now(timezone.utc)

        async def _persist(db) -> None:
            await _persist_indicators(db, symbol, results, now)
            await _refresh_market_metadata(db, symbol, spread_payload or {}, now)

        await run_db_task(_persist, celery=False)

        return f"{symbol}: ok indicators={len(results)}"


async def _run_one_cycle(concurrency: int, of_window: int) -> None:
    from ..database import run_db_task

    cycle_start = datetime.now(timezone.utc)
    try:
        symbols = await run_db_task(_collect_symbols, celery=False)

        if not symbols:
            logger.info("[MICRO-SCHED] no symbols to refresh — skipping cycle")
            return

        logger.info("[MICRO-SCHED] starting cycle for %d symbols (concurrency=%d)",
                    len(symbols), concurrency)

        semaphore = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(
            *[_refresh_one_symbol(s, semaphore, of_window) for s in symbols],
            return_exceptions=True,
        )

        ok = sum(1 for r in results if isinstance(r, str) and ": ok " in r)
        failed = sum(1 for r in results if isinstance(r, BaseException))
        duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info("[MICRO-SCHED] cycle done — %d/%d ok, %d exceptions, %.1fs",
                    ok, len(symbols), failed, duration)
    finally:
        _get_first_cycle_done_event().set()
        # Forward-signal the combined scheduler event so pipeline_scheduler_service
        # wait_for_first_cycle() can resolve even when combined scheduler is disabled.
        try:
            from .scheduler_service import _get_first_cycle_done_event as _combined_evt
            _combined_evt().set()
        except Exception:
            pass


async def _scheduler_loop() -> None:
    interval = _env_int("MICROSTRUCTURE_SCHEDULER_INTERVAL_SECONDS",
                        DEFAULT_INTERVAL_SECONDS)
    concurrency = _env_int("BACKGROUND_SCHEDULER_CONCURRENCY", DEFAULT_CONCURRENCY)
    first_run_delay = _env_int("MICROSTRUCTURE_SCHEDULER_FIRST_RUN_DELAY_SECONDS",
                               DEFAULT_FIRST_RUN_DELAY_SECONDS)
    of_window = _env_int("MICROSTRUCTURE_ORDER_FLOW_WINDOW_SECONDS",
                         DEFAULT_ORDER_FLOW_WINDOW_SECONDS)

    logger.info("[MICRO-SCHED] scheduler starting (interval=%ds, concurrency=%d, of_window=%ds)",
                interval, concurrency, of_window)

    try:
        await asyncio.sleep(first_run_delay)
    except asyncio.CancelledError:
        return

    while True:
        try:
            await _run_one_cycle(concurrency, of_window)
        except asyncio.CancelledError:
            logger.info("[MICRO-SCHED] scheduler cancelled — exiting loop")
            raise
        except Exception as exc:
            logger.exception("[MICRO-SCHED] cycle crashed: %s", exc)

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("[MICRO-SCHED] scheduler cancelled — exiting loop")
            raise


def start_microstructure_scheduler() -> Optional[asyncio.Task]:
    """Launch the microstructure scheduler as a background task."""
    global _scheduler_task

    if os.environ.get("SKIP_MICROSTRUCTURE_SCHEDULER") == "1":
        logger.info("[MICRO-SCHED] SKIP_MICROSTRUCTURE_SCHEDULER=1 — scheduler disabled")
        return None

    if _scheduler_task is not None and not _scheduler_task.done():
        logger.debug("[MICRO-SCHED] scheduler already running — reusing existing task")
        return _scheduler_task

    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(
        _scheduler_loop(), name="scalpyn-microstructure-scheduler"
    )
    return _scheduler_task


async def stop_microstructure_scheduler() -> None:
    """Cancel the microstructure scheduler task and wait for it to exit."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except (asyncio.CancelledError, Exception):
        pass
    _scheduler_task = None
