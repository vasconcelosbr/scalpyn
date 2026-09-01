"""Isolated, write-only research OHLCV tasks for native Gate 15m/1h data."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import text

from ..services.research_ohlcv_service import (
    GateClosedCandleBatch,
    SUPPORTED_TIMEFRAMES,
    fetch_gate_closed_candles,
    paced_request_delay,
    persist_gate_closed_batch,
    record_ingestion_observation,
    retention_days,
    target_candles,
    validate_retention_contract,
    validate_timeframe,
)
from .celery_app import celery_app
from .ohlcv_backfill import _run_async

logger = logging.getLogger(__name__)


def _recent_points() -> int:
    value = int(os.getenv("OHLCV_RESEARCH_CONTINUOUS_POINTS", "4"))
    if not 2 <= value <= 100:
        raise ValueError("OHLCV_RESEARCH_CONTINUOUS_POINTS must be 2..100")
    return value


async def _collect_async(timeframe: str) -> dict[str, Any]:
    validate_timeframe(timeframe)

    from ..database import CeleryAsyncSessionLocal
    from ..services import ohlcv_metrics
    from ..services.pool_service import get_active_pool_symbols

    started_at = datetime.now(timezone.utc)
    successes = 0
    failures = 0
    received = 0
    inserted = 0
    rejected_open = 0
    latest_close_time = None
    points = _recent_points()

    async with CeleryAsyncSessionLocal() as db:
        symbols = sorted(await get_active_pool_symbols(db, "spot"))
        if db.in_transaction():
            await db.rollback()

        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for symbol in symbols:
                try:
                    batch = await fetch_gate_closed_candles(
                        client,
                        symbol=symbol,
                        timeframe=timeframe,
                        points=points,
                    )
                    inserted_now = await persist_gate_closed_batch(db, batch)
                    successes += 1
                    received += len(batch.records)
                    inserted += inserted_now
                    rejected_open += batch.rejected_open_candles
                    if batch.latest_close_time is not None:
                        latest_close_time = max(
                            latest_close_time or batch.latest_close_time,
                            batch.latest_close_time,
                        )
                    ohlcv_metrics.record_received(
                        symbol, timeframe, len(batch.records)
                    )
                    ohlcv_metrics.record_persisted(
                        symbol, timeframe, inserted_now
                    )
                    if batch.availability_lag_seconds is not None:
                        ohlcv_metrics.record_latest_age(
                            symbol,
                            timeframe,
                            batch.availability_lag_seconds,
                        )
                    logger.info(
                        "[RESEARCH-OHLCV][OK] symbol=%s timeframe=%s "
                        "received=%d inserted=%d rejected_open=%d "
                        "close_time=%s available_at=%s lag_s=%s",
                        symbol,
                        timeframe,
                        len(batch.records),
                        inserted_now,
                        batch.rejected_open_candles,
                        batch.latest_close_time,
                        batch.observed_at,
                        batch.availability_lag_seconds,
                    )
                except Exception as exc:
                    failures += 1
                    observed_at = datetime.now(timezone.utc)
                    empty_batch = GateClosedCandleBatch(
                        symbol=symbol,
                        timeframe=timeframe,
                        observed_at=observed_at,
                        records=(),
                        rejected_open_candles=0,
                        rate_limit=None,
                        rate_limit_remaining=None,
                    )
                    try:
                        if db.in_transaction():
                            await db.rollback()
                        await record_ingestion_observation(
                            db,
                            batch=empty_batch,
                            inserted_rows=0,
                            status="error",
                            error_code=type(exc).__name__,
                        )
                    except Exception:
                        logger.exception(
                            "[RESEARCH-OHLCV] failed to persist error observation"
                        )
                    logger.exception(
                        "[RESEARCH-OHLCV][FAILED] symbol=%s timeframe=%s",
                        symbol,
                        timeframe,
                    )
                await paced_request_delay()

    summary = {
        "timeframe": timeframe,
        "target_symbols": len(symbols),
        "successful_symbols": successes,
        "failed_symbols": failures,
        "received_rows": received,
        "inserted_rows": inserted,
        "rejected_open_candles": rejected_open,
        "latest_close_time": latest_close_time,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc),
        "decision_dispatches": 0,
    }
    logger.info(
        "[RESEARCH-OHLCV][SUMMARY] %s",
        json.dumps(summary, default=str, sort_keys=True),
    )
    return summary


@celery_app.task(name="app.tasks.collect_research_ohlcv.collect_15m")
def collect_15m() -> str:
    return json.dumps(_run_async(_collect_async("15m")), default=str)


@celery_app.task(name="app.tasks.collect_research_ohlcv.collect_1h")
def collect_1h() -> str:
    return json.dumps(_run_async(_collect_async("1h")), default=str)


async def _retention_async() -> dict[str, Any]:
    from ..database import CeleryAsyncSessionLocal

    retention = validate_retention_contract()
    deleted: dict[str, int] = {}
    now = datetime.now(timezone.utc)
    async with CeleryAsyncSessionLocal() as db:
        for timeframe in SUPPORTED_TIMEFRAMES:
            cutoff = now - timedelta(days=retention[timeframe])
            result = await db.execute(
                text(
                    """
                    DELETE FROM ohlcv
                     WHERE timeframe = :timeframe
                       AND time < :cutoff
                    """
                ),
                {"timeframe": timeframe, "cutoff": cutoff},
            )
            deleted[timeframe] = max(int(result.rowcount or 0), 0)

        observation_days = int(
            os.getenv("OHLCV_RESEARCH_OBSERVATION_RETENTION_DAYS", "30")
        )
        if observation_days <= 0:
            raise ValueError(
                "OHLCV_RESEARCH_OBSERVATION_RETENTION_DAYS must be positive"
            )
        observation_cutoff = now - timedelta(days=observation_days)
        observations = await db.execute(
            text(
                "DELETE FROM ohlcv_ingestion_observations "
                "WHERE observed_at < :cutoff"
            ),
            {"cutoff": observation_cutoff},
        )
        snapshots = await db.execute(
            text(
                "DELETE FROM ohlcv_readiness_snapshots "
                "WHERE observed_at < :cutoff"
            ),
            {"cutoff": observation_cutoff},
        )
        await db.commit()

    result = {
        "retention_days": retention,
        "deleted_ohlcv_rows": deleted,
        "deleted_observations": max(int(observations.rowcount or 0), 0),
        "deleted_readiness_snapshots": max(int(snapshots.rowcount or 0), 0),
        "protected_timeframes": ["1m", "5m"],
    }
    logger.info(
        "[RESEARCH-OHLCV][RETENTION] %s",
        json.dumps(result, sort_keys=True),
    )
    return result


@celery_app.task(name="app.tasks.collect_research_ohlcv.enforce_retention")
def enforce_retention() -> str:
    return json.dumps(_run_async(_retention_async()), default=str)


_READINESS_SQL = text(
    """
    WITH active AS (
        SELECT DISTINCT symbol
          FROM pool_coins
         WHERE market_type = 'spot'
           AND is_active IS TRUE
    ),
    ranked AS (
        SELECT o.symbol,
               o.time,
               row_number() OVER (
                   PARTITION BY o.symbol ORDER BY o.time DESC
               ) AS rn
          FROM ohlcv o
          JOIN active a ON a.symbol = o.symbol
         WHERE o.exchange = 'gate.io'
           AND o.market_type = 'spot'
           AND o.timeframe = :timeframe
    ),
    recent AS (
        SELECT symbol, time
          FROM ranked
         WHERE rn <= :target_candles
    ),
    ordered AS (
        SELECT symbol,
               time,
               lead(time) OVER (PARTITION BY symbol ORDER BY time) AS next_time
          FROM recent
    ),
    per_symbol AS (
        SELECT a.symbol,
               count(o.time)::integer AS rows,
               max(o.time) AS latest_open,
               coalesce(sum(
                   greatest(
                       floor(extract(epoch FROM (o.next_time - o.time))
                             / :interval_seconds)::bigint - 1,
                       0
                   )
               ) FILTER (WHERE o.next_time IS NOT NULL), 0)::bigint AS gaps
          FROM active a
          LEFT JOIN ordered o ON o.symbol = a.symbol
         GROUP BY a.symbol
    )
    SELECT clock_timestamp() AS observed_at,
           :timeframe AS timeframe,
           :target_candles::integer AS target_candles,
           count(*)::integer AS target_symbols,
           count(*) FILTER (WHERE rows > 0)::integer AS present_symbols,
           count(*) FILTER (WHERE rows >= :target_candles)::integer
               AS target_ready_symbols,
           count(*) FILTER (WHERE rows >= 200)::integer AS ema200_ready_symbols,
           coalesce(sum(gaps), 0)::bigint AS total_gap_candles,
           coalesce(min(rows), 0)::integer AS minimum_rows,
           coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY rows), 0)
               AS median_rows,
           coalesce(max(rows), 0)::integer AS maximum_rows,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY extract(epoch FROM (
                   clock_timestamp()
                   - (latest_open + make_interval(secs => :interval_seconds))
               ))
           ) FILTER (WHERE latest_open IS NOT NULL) AS median_close_lag_seconds,
           percentile_cont(0.95) WITHIN GROUP (
               ORDER BY extract(epoch FROM (
                   clock_timestamp()
                   - (latest_open + make_interval(secs => :interval_seconds))
               ))
           ) FILTER (WHERE latest_open IS NOT NULL) AS p95_close_lag_seconds
      FROM per_symbol
    """
)


async def _capture_readiness_async() -> list[dict[str, Any]]:
    from ..database import CeleryAsyncSessionLocal

    snapshots: list[dict[str, Any]] = []
    async with CeleryAsyncSessionLocal() as db:
        for timeframe, interval_seconds in SUPPORTED_TIMEFRAMES.items():
            target = target_candles(timeframe)
            result = await db.execute(
                _READINESS_SQL,
                {
                    "timeframe": timeframe,
                    "target_candles": target,
                    "interval_seconds": interval_seconds,
                },
            )
            row = dict(result.mappings().one())
            await db.execute(
                text(
                    """
                    INSERT INTO ohlcv_readiness_snapshots
                        (observed_at, timeframe, target_candles,
                         target_symbols, present_symbols,
                         target_ready_symbols, ema200_ready_symbols,
                         total_gap_candles, minimum_rows, median_rows,
                         maximum_rows, median_close_lag_seconds,
                         p95_close_lag_seconds)
                    VALUES
                        (:observed_at, :timeframe, :target_candles,
                         :target_symbols, :present_symbols,
                         :target_ready_symbols, :ema200_ready_symbols,
                         :total_gap_candles, :minimum_rows, :median_rows,
                         :maximum_rows, :median_close_lag_seconds,
                         :p95_close_lag_seconds)
                    ON CONFLICT (observed_at, timeframe) DO NOTHING
                    """
                ),
                row,
            )
            snapshots.append(row)
        await db.commit()

    logger.info(
        "[RESEARCH-OHLCV][READINESS] %s",
        json.dumps(snapshots, default=str, sort_keys=True),
    )
    return snapshots


@celery_app.task(name="app.tasks.collect_research_ohlcv.capture_readiness")
def capture_readiness() -> str:
    return json.dumps(_run_async(_capture_readiness_async()), default=str)
