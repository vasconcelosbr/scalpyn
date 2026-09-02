"""Isolated Gate OHLCV tasks, including the closed/live dual run."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import text

from ..services.research_ohlcv_service import (
    CANONICAL_RESEARCH_TIMEFRAMES,
    GateClosedCandleBatch,
    SHADOW_STATE_TIMEFRAMES,
    STATE_CAPTURE_CONTRACT_VERSION,
    SUPPORTED_TIMEFRAMES,
    fetch_gate_closed_candles,
    paced_request_delay,
    persist_gate_closed_batch,
    persist_gate_state_batch,
    record_ingestion_observation,
    record_gate_state_error,
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


def _state_points(timeframe: str) -> int:
    value = int(
        os.getenv(f"OHLCV_STATE_{timeframe.upper()}_CONTINUOUS_POINTS", "4")
    )
    if not 2 <= value <= 100:
        raise ValueError(
            f"OHLCV_STATE_{timeframe.upper()}_CONTINUOUS_POINTS must be 2..100"
        )
    return value


def _state_symbol_concurrency() -> int:
    # Four in-flight requests plus the configured post-request pacing stays
    # below Gate's public-endpoint budget even when the pool is full.
    value = int(os.getenv("OHLCV_STATE_SYMBOL_CONCURRENCY", "4"))
    if not 1 <= value <= 20:
        raise ValueError("OHLCV_STATE_SYMBOL_CONCURRENCY must be 1..20")
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


async def _collect_state_shadow_async(timeframe: str) -> dict[str, Any]:
    """Collect one state-separated timeframe without canonical consumers."""
    if timeframe not in SHADOW_STATE_TIMEFRAMES:
        raise ValueError(f"unsupported dual-run timeframe {timeframe!r}")

    from ..database import CeleryAsyncSessionLocal
    from ..services import ohlcv_metrics
    from ..services.pool_service import get_active_pool_symbols

    started_at = datetime.now(timezone.utc)
    async with CeleryAsyncSessionLocal() as db:
        symbols = sorted(await get_active_pool_symbols(db, "spot"))
        finalization_delay_seconds = int(
            (
                await db.execute(
                    text(
                        """
                        SELECT finalization_delay_seconds
                          FROM ohlcv_capture_contracts
                         WHERE capture_contract_version = :version
                           AND mode = 'SHADOW'
                           AND canonical_read_enabled IS FALSE
                        """
                    ),
                    {"version": STATE_CAPTURE_CONTRACT_VERSION},
                )
            ).scalar_one()
        )
        if db.in_transaction():
            await db.rollback()

    semaphore = asyncio.Semaphore(_state_symbol_concurrency())
    timeout = httpx.Timeout(30.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async def fetch_one(symbol: str):
            async with semaphore:
                try:
                    batch = await fetch_gate_closed_candles(
                        client,
                        symbol=symbol,
                        timeframe=timeframe,
                        points=_state_points(timeframe),
                        finalization_delay_seconds=finalization_delay_seconds,
                    )
                    await paced_request_delay()
                    return symbol, batch, None
                except Exception as exc:
                    return symbol, None, exc

        fetched = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols))

    successes = 0
    failures = 0
    received_closed = 0
    received_live = 0
    inserted_closed = 0
    upserted_live = 0
    rejected_open = 0
    latest_close_time = None

    async with CeleryAsyncSessionLocal() as db:
        for symbol, batch, error in fetched:
            if error is not None or batch is None:
                failures += 1
                if db.in_transaction():
                    await db.rollback()
                await record_gate_state_error(
                    db,
                    symbol=symbol,
                    timeframe=timeframe,
                    observed_at=datetime.now(timezone.utc),
                    error_code=type(error).__name__ if error else "EmptyBatch",
                )
                logger.error(
                    "[OHLCV-STATE-SHADOW][FAILED] symbol=%s timeframe=%s error=%s",
                    symbol,
                    timeframe,
                    type(error).__name__ if error else "EmptyBatch",
                )
                continue

            try:
                inserted_now, live_now = await persist_gate_state_batch(db, batch)
            except Exception as exc:
                failures += 1
                if db.in_transaction():
                    await db.rollback()
                await record_gate_state_error(
                    db,
                    symbol=symbol,
                    timeframe=timeframe,
                    observed_at=datetime.now(timezone.utc),
                    error_code=type(exc).__name__,
                )
                logger.exception(
                    "[OHLCV-STATE-SHADOW][PERSIST-FAILED] symbol=%s timeframe=%s",
                    symbol,
                    timeframe,
                )
                continue

            successes += 1
            received_closed += len(batch.records)
            received_live += len(batch.live_records)
            inserted_closed += inserted_now
            upserted_live += live_now
            rejected_open += batch.rejected_open_candles
            if batch.latest_close_time is not None:
                latest_close_time = max(
                    latest_close_time or batch.latest_close_time,
                    batch.latest_close_time,
                )
            ohlcv_metrics.record_received(symbol, timeframe, len(batch.records))
            ohlcv_metrics.record_persisted(symbol, timeframe, inserted_now)
            if batch.availability_lag_seconds is not None:
                ohlcv_metrics.record_latest_age(
                    symbol, timeframe, batch.availability_lag_seconds
                )

    summary = {
        "mode": "SHADOW",
        "capture_contract_version": STATE_CAPTURE_CONTRACT_VERSION,
        "canonical_read_enabled": False,
        "finalization_delay_seconds": finalization_delay_seconds,
        "timeframe": timeframe,
        "target_symbols": len(symbols),
        "successful_symbols": successes,
        "failed_symbols": failures,
        "received_closed_rows": received_closed,
        "received_live_rows": received_live,
        "inserted_closed_rows": inserted_closed,
        "upserted_live_rows": upserted_live,
        "rejected_from_closed_rows": rejected_open,
        "latest_close_time": latest_close_time,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc),
        "decision_dispatches": 0,
    }
    logger.info(
        "[OHLCV-STATE-SHADOW][SUMMARY] %s",
        json.dumps(summary, default=str, sort_keys=True),
    )
    return summary


@celery_app.task(name="app.tasks.collect_research_ohlcv.collect_1m_shadow")
def collect_1m_shadow() -> str:
    return json.dumps(_run_async(_collect_state_shadow_async("1m")), default=str)


@celery_app.task(name="app.tasks.collect_research_ohlcv.collect_5m_shadow")
def collect_5m_shadow() -> str:
    return json.dumps(_run_async(_collect_state_shadow_async("5m")), default=str)


@celery_app.task(name="app.tasks.collect_research_ohlcv.collect_30m_shadow")
def collect_30m_shadow() -> str:
    return json.dumps(_run_async(_collect_state_shadow_async("30m")), default=str)


async def _retention_async() -> dict[str, Any]:
    from ..database import CeleryAsyncSessionLocal

    retention = validate_retention_contract()
    deleted: dict[str, int] = {}
    now = datetime.now(timezone.utc)
    async with CeleryAsyncSessionLocal() as db:
        for timeframe in CANONICAL_RESEARCH_TIMEFRAMES:
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
           CAST(:target_candles AS integer) AS target_candles,
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
        for timeframe in CANONICAL_RESEARCH_TIMEFRAMES:
            interval_seconds = SUPPORTED_TIMEFRAMES[timeframe]
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


_STATE_COMPARISON_SQL = text(
    """
    WITH contract AS (
        SELECT valid_from
          FROM ohlcv_capture_contracts
         WHERE capture_contract_version = :capture_contract_version
           AND mode = 'SHADOW'
           AND canonical_read_enabled IS FALSE
    ), shadow_rows AS (
        SELECT s.*
          FROM ohlcv_shadow s
          CROSS JOIN contract c
         WHERE s.timeframe = :timeframe
           AND s.capture_contract_version = :capture_contract_version
           AND s.time >= c.valid_from
    ), compared AS (
        SELECT s.time,
               o.time IS NOT NULL AS canonical_present,
               o.open IS NOT DISTINCT FROM s.open
               AND o.high IS NOT DISTINCT FROM s.high
               AND o.low IS NOT DISTINCT FROM s.low
               AND o.close IS NOT DISTINCT FROM s.close
               AND o.volume IS NOT DISTINCT FROM s.volume
               AND o.quote_volume IS NOT DISTINCT FROM s.quote_volume AS exact
          FROM shadow_rows s
          LEFT JOIN ohlcv o
            ON o.time = s.time
           AND o.symbol = s.symbol
           AND o.exchange = s.exchange
           AND o.timeframe = s.timeframe
           AND o.market_type = s.market_type
    )
    SELECT clock_timestamp() AS observed_at,
           :timeframe AS timeframe,
           :capture_contract_version AS capture_contract_version,
           c.valid_from,
           (SELECT count(*) FROM shadow_rows)::bigint AS shadow_rows,
           (SELECT count(*) FROM ohlcv o
             WHERE o.timeframe = :timeframe
               AND o.market_type = 'spot'
               AND o.time >= c.valid_from)::bigint AS canonical_rows,
           count(*) FILTER (WHERE canonical_present)::bigint AS compared_rows,
           count(*) FILTER (WHERE canonical_present AND exact)::bigint AS exact_rows,
           count(*) FILTER (WHERE canonical_present AND NOT exact)::bigint
               AS divergent_rows,
           count(*) FILTER (WHERE NOT canonical_present)::bigint
               AS missing_canonical_rows,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY extract(epoch FROM (
                   clock_timestamp() - (time + make_interval(secs => :interval_seconds))
               ))
           ) AS median_close_lag_seconds,
           percentile_cont(0.95) WITHIN GROUP (
               ORDER BY extract(epoch FROM (
                   clock_timestamp() - (time + make_interval(secs => :interval_seconds))
               ))
           ) AS p95_close_lag_seconds
      FROM compared
      CROSS JOIN contract c
     GROUP BY c.valid_from
    """
)


async def _capture_state_comparison_async() -> list[dict[str, Any]]:
    from ..database import CeleryAsyncSessionLocal

    snapshots: list[dict[str, Any]] = []
    async with CeleryAsyncSessionLocal() as db:
        for timeframe in SHADOW_STATE_TIMEFRAMES:
            result = await db.execute(
                _STATE_COMPARISON_SQL,
                {
                    "timeframe": timeframe,
                    "capture_contract_version": STATE_CAPTURE_CONTRACT_VERSION,
                    "interval_seconds": SUPPORTED_TIMEFRAMES[timeframe],
                },
            )
            row = result.mappings().one_or_none()
            if row is None:
                continue
            payload = dict(row)
            await db.execute(
                text(
                    """
                    INSERT INTO ohlcv_capture_comparison_snapshots
                        (observed_at, timeframe, capture_contract_version,
                         valid_from, shadow_rows, canonical_rows, compared_rows,
                         exact_rows, divergent_rows, missing_canonical_rows,
                         median_close_lag_seconds, p95_close_lag_seconds)
                    VALUES
                        (:observed_at, :timeframe, :capture_contract_version,
                         :valid_from, :shadow_rows, :canonical_rows,
                         :compared_rows, :exact_rows, :divergent_rows,
                         :missing_canonical_rows, :median_close_lag_seconds,
                         :p95_close_lag_seconds)
                    ON CONFLICT
                        (observed_at, timeframe, capture_contract_version)
                    DO NOTHING
                    """
                ),
                payload,
            )
            snapshots.append(payload)
        await db.commit()

    logger.info(
        "[OHLCV-STATE-SHADOW][COMPARISON] %s",
        json.dumps(snapshots, default=str, sort_keys=True),
    )
    return snapshots


@celery_app.task(
    name="app.tasks.collect_research_ohlcv.capture_state_comparison"
)
def capture_state_comparison() -> str:
    return json.dumps(
        _run_async(_capture_state_comparison_async()), default=str
    )
