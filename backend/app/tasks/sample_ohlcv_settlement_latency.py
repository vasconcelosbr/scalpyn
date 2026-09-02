"""R1.B: measure Gate settlement-revision latency, isolated from decisions.

Captures the same candle at fixed delays after its nominal close (10s, 30s,
60s, 120s, 300s) for a small representative sample of symbols and the three
dual-run timeframes, and records the raw field values seen at each delay.
This module has no indicator, score, signal, profile, or trading import, and
writes only to ``ohlcv_settlement_latency_samples`` -- it is not a decision
input and does not touch ``ohlcv``, ``ohlcv_shadow``, or ``ohlcv_live``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from ..services.research_ohlcv_service import (
    SHADOW_STATE_TIMEFRAMES,
    due_settlement_latency_targets,
    fetch_gate_raw_candle,
    paced_request_delay,
    persist_settlement_latency_sample,
)
from .celery_app import celery_app
from .ohlcv_backfill import _run_async

logger = logging.getLogger(__name__)

_DEFAULT_SAMPLE_SYMBOLS = (
    "BTC_USDT",   # highest liquidity in the active pool
    "SOL_USDT",   # top-tier alt
    "LINK_USDT",  # mid liquidity
    "NEAR_USDT",  # mid-low liquidity
    "TAO_USDT",   # low liquidity
    "XDC_USDT",   # lowest liquidity in the active pool
)


def sample_symbols() -> tuple[str, ...]:
    raw = os.getenv("OHLCV_LATENCY_SAMPLE_SYMBOLS", "")
    if not raw.strip():
        return _DEFAULT_SAMPLE_SYMBOLS
    symbols = tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    if not symbols:
        return _DEFAULT_SAMPLE_SYMBOLS
    return symbols


def _sample_concurrency() -> int:
    value = int(os.getenv("OHLCV_LATENCY_SAMPLE_CONCURRENCY", "4"))
    if not 1 <= value <= 20:
        raise ValueError("OHLCV_LATENCY_SAMPLE_CONCURRENCY must be 1..20")
    return value


async def _sample_async() -> dict[str, Any]:
    from ..database import CeleryAsyncSessionLocal

    now = datetime.now(timezone.utc)
    due: list[tuple[str, str, datetime, int, float]] = []
    for symbol in sample_symbols():
        for timeframe in SHADOW_STATE_TIMEFRAMES:
            for open_time, delay_target, delay_actual in due_settlement_latency_targets(
                timeframe=timeframe, now=now
            ):
                due.append((symbol, timeframe, open_time, delay_target, delay_actual))

    if not due:
        return {"due": 0, "fetched": 0, "found": 0, "errors": 0}

    semaphore = asyncio.Semaphore(_sample_concurrency())
    timeout = httpx.Timeout(15.0, connect=10.0)
    results: list[tuple[str, str, datetime, int, float, Any, Exception | None]] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        async def fetch_one(item: tuple[str, str, datetime, int, float]):
            symbol, timeframe, open_time, delay_target, delay_actual = item
            async with semaphore:
                try:
                    candle = await fetch_gate_raw_candle(
                        client,
                        symbol=symbol,
                        timeframe=timeframe,
                        target_open_time=open_time,
                    )
                    await paced_request_delay()
                    return (symbol, timeframe, open_time, delay_target,
                            delay_actual, candle, None)
                except Exception as exc:  # noqa: BLE001 - recorded, not raised
                    return (symbol, timeframe, open_time, delay_target,
                            delay_actual, None, exc)

        results = await asyncio.gather(*(fetch_one(item) for item in due))

    fetched = 0
    found = 0
    errors = 0
    async with CeleryAsyncSessionLocal() as db:
        for symbol, timeframe, open_time, delay_target, delay_actual, candle, error in results:
            if error is not None:
                errors += 1
                logger.error(
                    "[OHLCV-SETTLE-LATENCY][FAILED] symbol=%s timeframe=%s "
                    "delay=%s error=%s",
                    symbol, timeframe, delay_target, type(error).__name__,
                )
                continue
            fetched += 1
            if candle is not None:
                found += 1
            await persist_settlement_latency_sample(
                db,
                symbol=symbol,
                timeframe=timeframe,
                candle_open_time=open_time,
                delay_target_seconds=delay_target,
                delay_actual_seconds=delay_actual,
                observed_at=datetime.now(timezone.utc),
                candle=candle,
            )

    summary = {
        "due": len(due),
        "fetched": fetched,
        "found": found,
        "errors": errors,
        "symbols": list(sample_symbols()),
    }
    logger.info(
        "[OHLCV-SETTLE-LATENCY][SUMMARY] %s",
        json.dumps(summary, default=str, sort_keys=True),
    )
    return summary


@celery_app.task(
    name="app.tasks.sample_ohlcv_settlement_latency.sample_settlement_latency"
)
def sample_settlement_latency() -> str:
    return json.dumps(_run_async(_sample_async()), default=str)
