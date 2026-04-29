"""Internal periodic scheduler for OHLCV / indicator / market_metadata refresh.

Runs as an asyncio task inside the FastAPI lifespan so the platform does not
need a separate Celery worker.  Every interval it iterates the symbols of all
active watchlists, fetches OHLCV (1h, 200 candles via Gate→Binance merge),
computes indicators, persists OHLCV + indicators, and refreshes spread /
orderbook depth on market_metadata.

The scheduler is opt-out via SKIP_BACKGROUND_SCHEDULER=1 and tuneable via
BACKGROUND_SCHEDULER_INTERVAL_SECONDS (default 1800 = 30 min) and
BACKGROUND_SCHEDULER_CONCURRENCY (default 8).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, List, Optional

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 1800
DEFAULT_CONCURRENCY = 8
DEFAULT_FIRST_RUN_DELAY_SECONDS = 30
DEFAULT_OHLCV_LIMIT = 200
TIMEFRAME = "1h"

_scheduler_task: Optional[asyncio.Task] = None

# Set after the first refresh cycle has finished. Other in-process
# schedulers (e.g. pipeline_scheduler_service) await this so their first
# run lands on top of fresh OHLCV / indicators / market_metadata instead
# of racing the very first indicator computation. Lazily created so we do
# not bind it to a stale event loop at import time.
_first_cycle_done_event: Optional[asyncio.Event] = None


def _get_first_cycle_done_event() -> asyncio.Event:
    """Return the singleton event, creating it lazily on the running loop."""
    global _first_cycle_done_event
    if _first_cycle_done_event is None:
        _first_cycle_done_event = asyncio.Event()
    return _first_cycle_done_event


async def wait_for_first_cycle(timeout: Optional[float] = None) -> bool:
    """Block until the background scheduler has completed at least one
    refresh cycle. Returns True if the event fired, False on timeout.

    Safe to call even when the scheduler is disabled — callers should
    treat a False return as "fall back to your own time delay".
    """
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
        logger.warning("Invalid int for %s=%r — using default %d", name, raw, default)
        return default


async def _collect_watchlist_symbols(db) -> List[str]:
    """Union of every symbol referenced by any pipeline watchlist (any layer)."""
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


async def _persist_ohlcv(db, symbol: str, df: pd.DataFrame, exchange: str) -> None:
    """Insert candles with ON CONFLICT DO NOTHING (timeseries unique time+symbol)."""
    if df is None or df.empty:
        return
    for _, row in df.iterrows():
        try:
            await db.execute(text("""
                INSERT INTO ohlcv
                    (time, symbol, exchange, timeframe,
                     open, high, low, close, volume, quote_volume)
                VALUES
                    (:time, :symbol, :exchange, :timeframe,
                     :open, :high, :low, :close, :volume, :quote_volume)
                ON CONFLICT DO NOTHING
            """), {
                "time": row["time"],
                "symbol": symbol,
                "exchange": exchange,
                "timeframe": TIMEFRAME,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "quote_volume": float(
                    row.get("quote_volume")
                    if row.get("quote_volume") is not None
                    else float(row["close"]) * float(row["volume"])
                ),
            })
        except Exception as exc:
            logger.debug("[SCHED] OHLCV insert skipped for %s @ %s: %s",
                         symbol, row.get("time"), exc)


async def _persist_indicators(db, symbol: str, results: dict, when: datetime) -> None:
    if not results:
        return
    try:
        # SAVEPOINT: isolates a constraint / column error so the parent
        # transaction remains healthy for _refresh_market_metadata below.
        # ON CONFLICT target matches uq_indicators_time_symbol_timeframe unique index
        # created by init_db.  Falls back to SAVEPOINT rollback + warning if the
        # index does not yet exist (e.g. due to pre-existing duplicate rows).
        async with db.begin_nested():
            await db.execute(text("""
                INSERT INTO indicators (time, symbol, timeframe, indicators_json)
                VALUES (:time, :symbol, :timeframe, :payload)
                ON CONFLICT (time, symbol, timeframe)
                    DO UPDATE SET indicators_json = EXCLUDED.indicators_json
            """), {
                "time": when,
                "symbol": symbol,
                "timeframe": TIMEFRAME,
                "payload": json.dumps(results, default=str),
            })
    except Exception as exc:
        logger.warning("[SCHED] indicators insert failed for %s: %s", symbol, exc)


async def _refresh_market_metadata(db, symbol: str, df: pd.DataFrame,
                                   spread_payload: dict, when: datetime) -> None:
    last_close: Optional[float] = None
    if df is not None and not df.empty:
        try:
            last_close = float(df.iloc[-1]["close"])
        except Exception:
            last_close = None

    spread_pct = spread_payload.get("spread_pct") if spread_payload else None
    depth = spread_payload.get("orderbook_depth_usdt") if spread_payload else None
    if last_close is None and spread_pct is None and depth is None:
        return

    try:
        # SAVEPOINT: isolates a market_metadata failure from the rest of the session.
        async with db.begin_nested():
            await db.execute(text("""
                INSERT INTO market_metadata
                    (symbol, price, spread_pct, orderbook_depth_usdt, last_updated)
                VALUES
                    (:symbol, :price, :spread, :depth, :updated)
                ON CONFLICT (symbol) DO UPDATE SET
                    price = COALESCE(:price, market_metadata.price),
                    spread_pct = COALESCE(:spread, market_metadata.spread_pct),
                    orderbook_depth_usdt = COALESCE(:depth, market_metadata.orderbook_depth_usdt),
                    last_updated = :updated
            """), {
                "symbol": symbol,
                "price": last_close,
                "spread": spread_pct,
                "depth": depth,
                "updated": when,
            })
    except Exception as exc:
        logger.warning("[SCHED] market_metadata upsert failed for %s: %s", symbol, exc)


async def _refresh_one_symbol(symbol: str, semaphore: asyncio.Semaphore) -> str:
    """Refresh OHLCV + indicators + spread/depth for a single symbol."""
    from ..database import AsyncSessionLocal
    from ..services.feature_engine import FeatureEngine
    from ..services.market_data_service import market_data_service
    from ..services.seed_service import DEFAULT_INDICATORS

    async with semaphore:
        try:
            df = await market_data_service.fetch_ohlcv(
                symbol, TIMEFRAME, limit=DEFAULT_OHLCV_LIMIT,
            )
        except Exception as exc:
            logger.warning("[SCHED] fetch_ohlcv failed for %s: %s", symbol, exc)
            return f"{symbol}: fetch_failed"

        if df is None or df.empty:
            return f"{symbol}: no_data"

        try:
            spread_payload = await market_data_service.fetch_orderbook_metrics(symbol)
        except Exception as exc:
            logger.debug("[SCHED] orderbook fetch failed for %s: %s", symbol, exc)
            spread_payload = {}

        engine = FeatureEngine(DEFAULT_INDICATORS)
        try:
            results = engine.calculate(df, market_data=spread_payload or None) or {}
        except Exception as exc:
            logger.warning("[SCHED] FeatureEngine.calculate failed for %s: %s",
                           symbol, exc)
            results = {}

        # Make sure spread/depth land in indicators payload too (so consumers
        # that read indicators directly do not need a second JOIN).
        if spread_payload:
            for key in ("spread_pct", "orderbook_depth_usdt",
                        "market_data_source", "market_data_confidence"):
                if spread_payload.get(key) is not None and key not in results:
                    results[key] = spread_payload[key]

        now = datetime.now(timezone.utc)
        exchange_attr = df.attrs.get("exchange", "gate.io")

        async with AsyncSessionLocal() as db:
            await _persist_ohlcv(db, symbol, df, exchange_attr)
            await _persist_indicators(db, symbol, results, now)
            await _refresh_market_metadata(db, symbol, df, spread_payload, now)
            await db.commit()

        return (
            f"{symbol}: ok candles={len(df)} src={exchange_attr} "
            f"spread={'y' if spread_payload.get('spread_pct') is not None else 'n'}"
        )


async def _run_one_cycle(concurrency: int) -> None:
    from ..database import AsyncSessionLocal

    cycle_start = datetime.now(timezone.utc)

    # Always signal readiness on cycle exit — even when the cycle is
    # skipped due to an empty symbol set or a transient DB error. Otherwise
    # downstream waiters (pipeline_scheduler_service) would deadlock on a
    # fresh DB where market_metadata / pipeline_watchlist_assets have not
    # been populated yet (the very state this scheduler is supposed to
    # repair).  Using try/finally guarantees the event is set even on
    # CancelledError / unexpected exceptions.
    try:
        async with AsyncSessionLocal() as db:
            symbols = await _collect_watchlist_symbols(db)

        if not symbols:
            logger.info("[SCHED] no symbols to refresh — skipping cycle")
            return

        logger.info("[SCHED] starting refresh cycle for %d symbols (concurrency=%d)",
                    len(symbols), concurrency)

        semaphore = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(
            *[_refresh_one_symbol(s, semaphore) for s in symbols],
            return_exceptions=True,
        )

        ok = sum(1 for r in results if isinstance(r, str) and ": ok " in r)
        failed = sum(1 for r in results if isinstance(r, BaseException))
        duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()

        logger.info(
            "[SCHED] cycle finished — %d/%d ok, %d exceptions, %.1fs",
            ok, len(symbols), failed, duration,
        )
    finally:
        # Signal any other in-process scheduler waiting on us (notably the
        # pipeline scheduler) that this cycle has run.  This must fire on
        # EVERY cycle exit, including the empty-symbols early return, to
        # avoid the readiness deadlock described above.
        _get_first_cycle_done_event().set()


async def _scheduler_loop() -> None:
    interval = _env_int("BACKGROUND_SCHEDULER_INTERVAL_SECONDS",
                        DEFAULT_INTERVAL_SECONDS)
    concurrency = _env_int("BACKGROUND_SCHEDULER_CONCURRENCY",
                           DEFAULT_CONCURRENCY)
    first_run_delay = _env_int("BACKGROUND_SCHEDULER_FIRST_RUN_DELAY_SECONDS",
                               DEFAULT_FIRST_RUN_DELAY_SECONDS)

    logger.info(
        "[SCHED] background scheduler starting "
        "(interval=%ds, concurrency=%d, first_run_delay=%ds)",
        interval, concurrency, first_run_delay,
    )

    try:
        await asyncio.sleep(first_run_delay)
    except asyncio.CancelledError:
        return

    while True:
        try:
            await _run_one_cycle(concurrency)
        except asyncio.CancelledError:
            logger.info("[SCHED] scheduler cancelled — exiting loop")
            raise
        except Exception as exc:
            logger.exception("[SCHED] cycle crashed: %s", exc)

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("[SCHED] scheduler cancelled — exiting loop")
            raise


def start_background_scheduler() -> Optional[asyncio.Task]:
    """Launch the combined (legacy) scheduler as a background task.

    By default the combined scheduler is DISABLED when the dual-scheduler
    architecture is active (structural + microstructure).  Set
    ``ENABLE_COMBINED_SCHEDULER=1`` to re-enable it (e.g. for debugging or
    during a rollback).

    Returns the task handle (so the lifespan can cancel it on shutdown), or
    None when the scheduler is disabled.
    """
    global _scheduler_task

    if os.environ.get("SKIP_BACKGROUND_SCHEDULER") == "1":
        logger.info("[SCHED] SKIP_BACKGROUND_SCHEDULER=1 — combined scheduler disabled")
        return None

    # The combined scheduler is opt-in when the dual-scheduler is present.
    # Without ENABLE_COMBINED_SCHEDULER=1 it stays dormant but its
    # wait_for_first_cycle() is still functional (forwarded by the new schedulers).
    if os.environ.get("ENABLE_COMBINED_SCHEDULER") != "1":
        logger.info(
            "[SCHED] combined scheduler inactive (ENABLE_COMBINED_SCHEDULER not set); "
            "structural + microstructure schedulers handle refresh"
        )
        return None

    if _scheduler_task is not None and not _scheduler_task.done():
        logger.debug("[SCHED] scheduler already running — reusing existing task")
        return _scheduler_task

    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(_scheduler_loop(),
                                       name="scalpyn-background-scheduler")
    return _scheduler_task


async def stop_background_scheduler() -> None:
    """Cancel the scheduler task and wait for it to exit."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _scheduler_task = None
