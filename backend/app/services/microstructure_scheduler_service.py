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
from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 300        # 5 min
DEFAULT_CONCURRENCY = 8
DEFAULT_FIRST_RUN_DELAY_SECONDS = 15  # fire quickly after structural
DEFAULT_OHLCV_LIMIT = 100             # 100 × 5m ≈ 8 h of data
TIMEFRAME = "5m"
SCHEDULER_GROUP = "microstructure"

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
        logger.warning("[MICRO-SCHED] Invalid int for %s=%r — using default %d",
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
        logger.warning("[MICRO-SCHED] indicators insert failed for %s: %s", symbol, exc)


async def _refresh_market_metadata(db, symbol: str,
                                   spread_payload: dict, when: datetime) -> None:
    spread_pct = spread_payload.get("spread_pct")
    depth = spread_payload.get("orderbook_depth_usdt")
    if spread_pct is None and depth is None:
        return
    try:
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
        logger.debug("[MICRO-SCHED] market_metadata upsert skipped for %s: %s", symbol, exc)


async def _refresh_one_symbol(symbol: str, semaphore: asyncio.Semaphore) -> str:
    from ..database import AsyncSessionLocal
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

        if not results:
            return f"{symbol}: no_data"

        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            await _persist_indicators(db, symbol, results, now)
            await _refresh_market_metadata(db, symbol, spread_payload or {}, now)
            await db.commit()

        return f"{symbol}: ok indicators={len(results)}"


async def _run_one_cycle(concurrency: int) -> None:
    from ..database import AsyncSessionLocal

    cycle_start = datetime.now(timezone.utc)
    try:
        async with AsyncSessionLocal() as db:
            symbols = await _collect_symbols(db)

        if not symbols:
            logger.info("[MICRO-SCHED] no symbols to refresh — skipping cycle")
            return

        logger.info("[MICRO-SCHED] starting cycle for %d symbols (concurrency=%d)",
                    len(symbols), concurrency)

        semaphore = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(
            *[_refresh_one_symbol(s, semaphore) for s in symbols],
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

    logger.info("[MICRO-SCHED] scheduler starting (interval=%ds, concurrency=%d)",
                interval, concurrency)

    try:
        await asyncio.sleep(first_run_delay)
    except asyncio.CancelledError:
        return

    while True:
        try:
            await _run_one_cycle(concurrency)
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
