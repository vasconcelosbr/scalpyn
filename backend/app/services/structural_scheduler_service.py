"""Structural indicator scheduler — slow indicators, 1h OHLCV, 15-min cadence.

Computes RSI, ADX, EMA, ATR, MACD, Bollinger Bands, Parabolic SAR, Z-score,
OBV, and Stochastic for every active symbol.  Persists results to the
``indicators`` table with ``scheduler_group = 'structural'``.

Disable via SKIP_STRUCTURAL_SCHEDULER=1.
Tune cadence via STRUCTURAL_SCHEDULER_INTERVAL_SECONDS (default 900 = 15 min).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 900        # 15 min
DEFAULT_CONCURRENCY = 8
DEFAULT_FIRST_RUN_DELAY_SECONDS = 30
DEFAULT_OHLCV_LIMIT = 200
TIMEFRAME = "1h"
SCHEDULER_GROUP = "structural"

_scheduler_task: Optional[asyncio.Task] = None

_first_cycle_done_event: Optional[asyncio.Event] = None


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
        logger.warning("[STRUCT-SCHED] Invalid int for %s=%r — using default %d",
                       name, raw, default)
        return default


async def _collect_symbols(db) -> List[str]:
    rows = (await db.execute(text("""
        SELECT DISTINCT symbol
        FROM pipeline_watchlist_assets
        WHERE symbol IS NOT NULL AND symbol <> ''
        UNION
        SELECT DISTINCT symbol
        FROM market_metadata
        WHERE symbol IS NOT NULL AND symbol <> ''
        LIMIT 500
    """))).fetchall()
    return [r.symbol for r in rows]


async def _persist_indicators(db, symbol: str, results: dict, when: datetime) -> None:
    if not results:
        return
    payload = json.dumps(results, default=str)
    try:
        # SAVEPOINT: isolates a constraint error so the parent transaction
        # remains healthy for _refresh_market_metadata below.
        async with db.begin_nested():
            await db.execute(text("""
                INSERT INTO indicators
                    (time, symbol, timeframe, indicators_json, scheduler_group)
                VALUES
                    (:time, :symbol, :timeframe, :payload, :grp)
                ON CONFLICT DO NOTHING
            """), {
                "time": when,
                "symbol": symbol,
                "timeframe": TIMEFRAME,
                "payload": payload,
                "grp": SCHEDULER_GROUP,
            })
    except Exception as exc:
        logger.warning("[STRUCT-SCHED] indicators insert failed for %s: %s", symbol, exc)


async def _refresh_market_metadata(db, symbol: str, df: pd.DataFrame, when: datetime) -> None:
    if df is None or df.empty:
        return
    try:
        # SAVEPOINT: isolates a market_metadata failure from the rest of the session.
        async with db.begin_nested():
            last_close = float(df.iloc[-1]["close"])
            await db.execute(text("""
                INSERT INTO market_metadata (symbol, price, last_updated)
                VALUES (:symbol, :price, :updated)
                ON CONFLICT (symbol) DO UPDATE SET
                    price = COALESCE(:price, market_metadata.price),
                    last_updated = :updated
            """), {"symbol": symbol, "price": last_close, "updated": when})
    except Exception as exc:
        logger.debug("[STRUCT-SCHED] market_metadata upsert skipped for %s: %s", symbol, exc)


async def _refresh_one_symbol(symbol: str, semaphore: asyncio.Semaphore) -> str:
    from ..database import run_db_task
    from ..services.feature_engine import FeatureEngine
    from ..services.market_data_service import market_data_service
    from ..services.seed_service import DEFAULT_INDICATORS

    async with semaphore:
        try:
            df = await market_data_service.fetch_ohlcv(symbol, TIMEFRAME,
                                                        limit=DEFAULT_OHLCV_LIMIT)
        except Exception as exc:
            logger.warning("[STRUCT-SCHED] fetch_ohlcv failed for %s: %s", symbol, exc)
            return f"{symbol}: fetch_failed"

        if df is None or df.empty:
            return f"{symbol}: no_data"

        engine = FeatureEngine(DEFAULT_INDICATORS)
        try:
            results = engine.calculate(df, group=SCHEDULER_GROUP) or {}
        except Exception as exc:
            logger.warning("[STRUCT-SCHED] FeatureEngine failed for %s: %s", symbol, exc)
            results = {}

        now = datetime.now(timezone.utc)

        async def _persist(db) -> None:
            await _persist_indicators(db, symbol, results, now)
            await _refresh_market_metadata(db, symbol, df, now)

        await run_db_task(_persist, celery=False)

        return f"{symbol}: ok indicators={len(results)}"


async def _run_one_cycle(concurrency: int) -> None:
    from ..database import run_db_task

    cycle_start = datetime.now(timezone.utc)
    try:
        symbols = await run_db_task(_collect_symbols, celery=False)

        if not symbols:
            logger.info("[STRUCT-SCHED] no symbols to refresh — skipping cycle")
            return

        logger.info("[STRUCT-SCHED] starting cycle for %d symbols (concurrency=%d)",
                    len(symbols), concurrency)

        semaphore = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(
            *[_refresh_one_symbol(s, semaphore) for s in symbols],
            return_exceptions=True,
        )

        ok = sum(1 for r in results if isinstance(r, str) and ": ok " in r)
        failed = sum(1 for r in results if isinstance(r, BaseException))
        duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info("[STRUCT-SCHED] cycle done — %d/%d ok, %d exceptions, %.1fs",
                    ok, len(symbols), failed, duration)
    finally:
        _get_first_cycle_done_event().set()
        # Also forward-signal the combined scheduler event so
        # pipeline_scheduler_service.wait_for_first_cycle() resolves.
        try:
            from .scheduler_service import _get_first_cycle_done_event as _combined_evt
            _combined_evt().set()
        except Exception:
            pass


async def _scheduler_loop() -> None:
    interval = _env_int("STRUCTURAL_SCHEDULER_INTERVAL_SECONDS",
                        DEFAULT_INTERVAL_SECONDS)
    concurrency = _env_int("BACKGROUND_SCHEDULER_CONCURRENCY", DEFAULT_CONCURRENCY)
    first_run_delay = _env_int("BACKGROUND_SCHEDULER_FIRST_RUN_DELAY_SECONDS",
                               DEFAULT_FIRST_RUN_DELAY_SECONDS)

    logger.info("[STRUCT-SCHED] scheduler starting (interval=%ds, concurrency=%d)",
                interval, concurrency)

    try:
        await asyncio.sleep(first_run_delay)
    except asyncio.CancelledError:
        return

    while True:
        try:
            await _run_one_cycle(concurrency)
        except asyncio.CancelledError:
            logger.info("[STRUCT-SCHED] scheduler cancelled — exiting loop")
            raise
        except Exception as exc:
            logger.exception("[STRUCT-SCHED] cycle crashed: %s", exc)

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("[STRUCT-SCHED] scheduler cancelled — exiting loop")
            raise


def start_structural_scheduler() -> Optional[asyncio.Task]:
    """Launch the structural scheduler as a background task."""
    global _scheduler_task

    if os.environ.get("SKIP_STRUCTURAL_SCHEDULER") == "1":
        logger.info("[STRUCT-SCHED] SKIP_STRUCTURAL_SCHEDULER=1 — scheduler disabled")
        return None

    if _scheduler_task is not None and not _scheduler_task.done():
        logger.debug("[STRUCT-SCHED] scheduler already running — reusing existing task")
        return _scheduler_task

    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(
        _scheduler_loop(), name="scalpyn-structural-scheduler"
    )
    return _scheduler_task


async def stop_structural_scheduler() -> None:
    """Cancel the structural scheduler task and wait for it to exit."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except (asyncio.CancelledError, Exception):
        pass
    _scheduler_task = None
