"""Canonical closed-candle collectors that own the Spot MTF compute chain."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import text

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
_TF_SECONDS = {"15m": 900, "1h": 3600}
_CAPTURE_CONTRACT_VERSION = "spot_mtf_closed_ohlcv_v2"


def collection_fetch_limit(indicator_config: dict, timeframe: str) -> int:
    """Include headroom for the exchange's current, still-open candle."""
    from .compute_mtf_indicators import required_warmup_candles

    return required_warmup_candles(indicator_config, timeframe) + 1


async def collect_timeframe(timeframe: str) -> dict:
    if timeframe not in _TF_SECONDS:
        raise ValueError("MTF_TIMEFRAME_UNSUPPORTED")
    from ..database import CeleryAsyncSessionLocal
    from ..services.market_data_service import market_data_service
    from ..services.pool_service import get_active_pool_symbols
    from .compute_mtf_indicators import (
        _load_governed_indicator_config,
        required_warmup_candles,
    )

    async with CeleryAsyncSessionLocal() as db:
        contract_active = await db.scalar(text("""
            SELECT EXISTS (
              SELECT 1 FROM ohlcv_capture_contracts
               WHERE capture_contract_version = :version
                 AND mode = 'CANONICAL'
                 AND canonical_read_enabled IS TRUE
                 AND valid_from <= clock_timestamp()
            )
        """), {"version": _CAPTURE_CONTRACT_VERSION})
        if not contract_active:
            return {
                "timeframe": timeframe,
                "status": "CAPTURE_CONTRACT_NOT_YET_VALID",
                "capture_contract_version": _CAPTURE_CONTRACT_VERSION,
                "successful_symbols": 0,
            }
        symbols = sorted(await get_active_pool_symbols(db, "spot"))
        indicator_config, _ = await _load_governed_indicator_config(db)
        required_warmup = required_warmup_candles(indicator_config, timeframe)
        fetch_limit = collection_fetch_limit(indicator_config, timeframe)
        if db.in_transaction():
            await db.rollback()

    duration = timedelta(seconds=_TF_SECONDS[timeframe])
    now = datetime.now(timezone.utc)
    batches: list[tuple[str, str, list[dict]]] = []
    failed = 0
    for symbol in symbols:
        try:
            frame = await market_data_service.fetch_ohlcv(
                symbol, timeframe, limit=fetch_limit
            )
            if frame is None or frame.empty:
                failed += 1
                continue
            exchange = str(frame.attrs.get("exchange") or "gate.io")
            if exchange != "gate.io":
                failed += 1
                logger.warning(
                    "[MTF-COLLECT-%s] source rejected symbol=%s source=%s",
                    timeframe,
                    symbol,
                    exchange,
                )
                continue
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
                     open, high, low, close, volume, quote_volume,
                     is_closed, ingested_at, capture_contract_version)
                VALUES
                    (:time, :symbol, :exchange, :timeframe, :market_type,
                     :open, :high, :low, :close, :volume, :quote_volume,
                     TRUE, clock_timestamp(), :capture_contract_version)
                ON CONFLICT (time, symbol, exchange, timeframe) DO UPDATE SET
                    exchange = EXCLUDED.exchange,
                    market_type = EXCLUDED.market_type,
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    quote_volume = EXCLUDED.quote_volume,
                    is_closed = TRUE,
                    ingested_at = EXCLUDED.ingested_at,
                    capture_contract_version = EXCLUDED.capture_contract_version
                WHERE ohlcv.capture_contract_version IS DISTINCT FROM
                      EXCLUDED.capture_contract_version
            """), [
                {**row, "capture_contract_version": _CAPTURE_CONTRACT_VERSION}
                for row in rows
            ])
            persisted += len(rows)
        await db.commit()
    result = {
        "timeframe": timeframe,
        "target_symbols": len(symbols),
        "successful_symbols": len(batches),
        "failed_symbols": failed,
        "closed_rows_submitted": persisted,
        "open_candles_rejected": True,
        "capture_contract_version": _CAPTURE_CONTRACT_VERSION,
        "required_warmup_candles": required_warmup,
        "fetch_limit": fetch_limit,
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
