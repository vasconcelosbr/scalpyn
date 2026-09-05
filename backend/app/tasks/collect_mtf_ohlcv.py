"""Canonical closed-candle collectors that own the Spot MTF compute chain."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
_TF_SECONDS = {"15m": 900, "1h": 3600}


async def collect_timeframe(timeframe: str) -> dict:
    if timeframe not in _TF_SECONDS:
        raise ValueError("MTF_TIMEFRAME_UNSUPPORTED")
    from ..database import CeleryAsyncSessionLocal
    from ..services.market_data_service import market_data_service
    from ..services.pool_service import get_active_pool_symbols

    async with CeleryAsyncSessionLocal() as db:
        symbols = sorted(await get_active_pool_symbols(db, "spot"))
        if db.in_transaction():
            await db.rollback()

    duration = timedelta(seconds=_TF_SECONDS[timeframe])
    now = datetime.now(timezone.utc)
    batches: list[tuple[str, str, list[dict]]] = []
    failed = 0
    for symbol in symbols:
        try:
            frame = await market_data_service.fetch_ohlcv(
                symbol, timeframe, limit=500
            )
            if frame is None or frame.empty:
                failed += 1
                continue
            exchange = str(frame.attrs.get("exchange") or "gate.io")
            prepared = []
            for row in frame.to_dict("records"):
                candle_time = pd.to_datetime(row["time"], utc=True).to_pydatetime()
                if candle_time + duration > now:
                    continue
                prepared.append({
                    "time": candle_time,
                    "symbol": symbol,
                    "exchange": exchange,
                    "timeframe": timeframe,
                    "market_type": "spot",
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
            if prepared:
                batches.append((symbol, exchange, prepared))
        except Exception:
            failed += 1
            logger.exception("[MTF-COLLECT-%s] failed symbol=%s", timeframe, symbol)

    persisted = 0
    async with CeleryAsyncSessionLocal() as db:
        for _symbol, _exchange, rows in batches:
            await db.execute(text("""
                INSERT INTO ohlcv
                    (time, symbol, exchange, timeframe, market_type,
                     open, high, low, close, volume, quote_volume)
                VALUES
                    (:time, :symbol, :exchange, :timeframe, :market_type,
                     :open, :high, :low, :close, :volume, :quote_volume)
                ON CONFLICT DO NOTHING
            """), rows)
            persisted += len(rows)
        await db.commit()
    result = {
        "timeframe": timeframe,
        "target_symbols": len(symbols),
        "successful_symbols": len(batches),
        "failed_symbols": failed,
        "closed_rows_submitted": persisted,
        "open_candles_rejected": True,
    }
    logger.info("[MTF-COLLECT] %s", result)
    return result


def _run(coro):
    from .compute_indicators import _run_async

    return _run_async(coro)


def _collect_and_chain(timeframe: str) -> dict:
    result = _run(collect_timeframe(timeframe))
    if result["successful_symbols"]:
        from . import task_dispatch

        task_dispatch.enqueue(
            f"app.tasks.compute_mtf_indicators.compute_{timeframe}",
            dedup_key=f"compute-mtf-{timeframe}",
            ttl_seconds=_TF_SECONDS[timeframe],
        )
        result["compute_enqueued"] = True
    else:
        result["compute_enqueued"] = False
    return result


@celery_app.task(name="app.tasks.collect_mtf_ohlcv.collect_15m")
def collect_15m():
    return _collect_and_chain("15m")


@celery_app.task(name="app.tasks.collect_mtf_ohlcv.collect_1h")
def collect_1h():
    return _collect_and_chain("1h")
