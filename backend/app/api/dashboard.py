"""Operational performance dashboard API (Task #224).

Read-only aggregations over `ohlcv`, `decisions_log`, `trade_tracking` and
`trade_simulations`. Powers the Next.js page at `/dashboard/performance`.

Constraints from Task #224 protection block:
  * Does NOT import `indicators_provider`, `pipeline_scan`, `score_engine`,
    `block_engine`, `evaluate_signals`, `execute_buy` or `fetch_merged_indicators`.
  * Does NOT mutate state — every endpoint is a SELECT.
  * Does NOT touch Celery, queues or scheduler.
  * Does NOT alter the schema of any table.

Auth: every endpoint depends on `get_current_user_id` (login required) but
data is operational/global — these dashboards describe the *system*, not a
user portfolio.  The per-user portfolio lives at `/api/analytics/dashboard`.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas.dashboard import (
    BlockReason,
    DecisionsResponse,
    HealthResponse,
    MlDatasetItem,
    MlDatasetResponse,
    OhlcvBucket,
    OhlcvRateResponse,
    OperationalEventsResponse,
    OperationalOverviewResponse,
    ScoreBucket,
    SnapshotEnvelope,
    SystemStatusResponse,
    TradeComparisonItem,
    TradeComparisonResponse,
    TradesAggResponse,
)
from ..services.operational_snapshot import get_service as get_ops_service
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

# Health thresholds (segundos).  10 min = ok, 10–20 min = warn, >20 min = critical.
# Loosened in Task #225 after observing that legitimate Celery/collect_all cycles
# can stretch up to 12-14 min during multi-symbol catch-ups; the previous 6/10
# thresholds were paging on healthy operation.  Mirrored on the frontend banner.
HEALTH_OK_SECONDS = 10 * 60
HEALTH_WARN_SECONDS = 20 * 60


def _classify_health(delay_seconds: Optional[float]) -> tuple[str, str]:
    if delay_seconds is None:
        return "unknown", "Sem dados"
    if delay_seconds < HEALTH_OK_SECONDS:
        return "ok", "Pipeline saudável"
    if delay_seconds <= HEALTH_WARN_SECONDS:
        return "warn", "Atrasado"
    return "critical", "Parado"


# ─────────────────────────────────────────────────────────────────────────────
# 1. /health
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/health", response_model=HealthResponse)
async def get_health(
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
):
    # `last_candle` MUST come from the full timeframe scope — restricting it
    # to the 15-min window makes a stopped pipeline silently return NULL and
    # be classified as "unknown" instead of "critical / Parado".  The window
    # filter is only used for the in-window row / symbol counters.
    sql = text(
        """
        WITH win AS (
            SELECT COUNT(*)::int               AS rows_window,
                   COUNT(DISTINCT symbol)::int AS distinct_symbols
            FROM ohlcv
            WHERE timeframe = '5m'
              AND time > NOW() - INTERVAL '15 minutes'
        ),
        last_c AS (
            SELECT MAX(time) AS last_candle
            FROM ohlcv
            WHERE timeframe = '5m'
        )
        SELECT win.rows_window,
               win.distinct_symbols,
               last_c.last_candle,
               EXTRACT(EPOCH FROM (NOW() - last_c.last_candle))::float AS delay_seconds
        FROM win, last_c
        """
    )
    row = (await db.execute(sql)).one()
    delay = float(row.delay_seconds) if row.delay_seconds is not None else None
    status_code, status_label = _classify_health(delay)
    return HealthResponse(
        rows_window=int(row.rows_window or 0),
        distinct_symbols=int(row.distinct_symbols or 0),
        last_candle=row.last_candle,
        delay_seconds=delay,
        status=status_code,
        status_label=status_label,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. /system-status
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/system-status", response_model=SystemStatusResponse)
async def get_system_status(
    _user_id: UUID = Depends(get_current_user_id),
):
    """Backwards-compatible system status — now snapshot-backed.

    Reads only from ``OperationalSnapshotService`` cache.  Does NOT open a
    Redis connection or run a DB query at request time.  This was an
    invariant introduced by Task #225 (no inline Celery/Redis probes in
    HTTP handlers — see replit.md gotcha).  The response shape is
    preserved for the legacy MonitoringTab clients.
    """
    return SystemStatusResponse(**get_ops_service().get_system_status_view())


# ─────────────────────────────────────────────────────────────────────────────
# 3. /ohlcv-rate
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/ohlcv-rate", response_model=OhlcvRateResponse)
async def get_ohlcv_rate(
    minutes: int = Query(60, ge=5, le=240),
    timeframe: str = Query("5m"),
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
):
    sql = text(
        """
        SELECT
            date_trunc('minute', time) AS bucket,
            COUNT(*)::int              AS candles
        FROM ohlcv
        WHERE timeframe = :tf
          AND time > NOW() - (:minutes || ' minutes')::interval
        GROUP BY bucket
        ORDER BY bucket
        """
    )
    rows = (await db.execute(sql, {"tf": timeframe, "minutes": minutes})).all()
    buckets = [OhlcvBucket(bucket=r.bucket, candles=int(r.candles)) for r in rows]
    return OhlcvRateResponse(
        window_minutes=minutes,
        timeframe=timeframe,
        total_candles=sum(b.candles for b in buckets),
        buckets=buckets,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. /decisions
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/decisions", response_model=DecisionsResponse)
async def get_decisions(
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
):
    agg_sql = text(
        """
        SELECT
            COUNT(*)::int                                                     AS total,
            COALESCE(SUM(CASE WHEN decision = 'ALLOW' THEN 1 ELSE 0 END), 0)::int AS allow_count,
            COALESCE(SUM(CASE WHEN decision = 'BLOCK' THEN 1 ELSE 0 END), 0)::int AS block_count,
            AVG(score)::float                                                 AS avg_score
        FROM decisions_log
        WHERE created_at > NOW() - (:hours || ' hours')::interval
        """
    )
    agg = (await db.execute(agg_sql, {"hours": hours})).one()
    total = int(agg.total or 0)
    allow = int(agg.allow_count or 0)
    block = int(agg.block_count or 0)

    dist_sql = text(
        """
        SELECT
            CASE
                WHEN score IS NULL  THEN 'n/a'
                WHEN score < 20     THEN '0-20'
                WHEN score < 40     THEN '20-40'
                WHEN score < 60     THEN '40-60'
                WHEN score < 80     THEN '60-80'
                ELSE '80-100'
            END AS bucket,
            COUNT(*)::int AS count
        FROM decisions_log
        WHERE created_at > NOW() - (:hours || ' hours')::interval
        GROUP BY bucket
        ORDER BY bucket
        """
    )
    dist_rows = (await db.execute(dist_sql, {"hours": hours})).all()
    score_distribution = [ScoreBucket(bucket=r.bucket, count=int(r.count)) for r in dist_rows]

    # Top motivos de bloqueio extraídos do JSONB `reasons`. Estrutura típica:
    # {"failed": ["reason_a", "reason_b"]} — caímos para a chave `failed`
    # quando existir, senão pegamos as próprias chaves do dict.
    # Set-returning functions cannot live inside COALESCE/CASE in the SELECT
    # list — must be lateral-joined.  We extract from `reasons.failed` when
    # it's an array; otherwise the LATERAL emits zero rows for that decision
    # and it's silently dropped, which is the desired behavior.
    reasons_sql = text(
        """
        SELECT reason, COUNT(*)::int AS count
        FROM decisions_log d
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(d.reasons -> 'failed') = 'array'
                 THEN d.reasons -> 'failed'
                 ELSE '[]'::jsonb
            END
        ) AS reason
        WHERE d.decision = 'BLOCK'
          AND d.created_at > NOW() - (:hours || ' hours')::interval
          AND d.reasons IS NOT NULL
        GROUP BY reason
        ORDER BY count DESC
        LIMIT 10
        """
    )
    try:
        reasons_rows = (await db.execute(reasons_sql, {"hours": hours})).all()
        top_block_reasons = [
            BlockReason(reason=r.reason, count=int(r.count)) for r in reasons_rows
        ]
    except Exception as exc:
        # JSONB shape may vary in early data; degrade gracefully.
        logger.warning("Failed to extract top block reasons: %s", exc)
        top_block_reasons = []

    return DecisionsResponse(
        window_hours=hours,
        total=total,
        allow=allow,
        block=block,
        allow_rate=(allow / total) if total else 0.0,
        avg_score=float(agg.avg_score) if agg.avg_score is not None else None,
        score_distribution=score_distribution,
        top_block_reasons=top_block_reasons,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. /trades
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/trades", response_model=TradesAggResponse)
async def get_trades(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
):
    agg_sql = text(
        """
        SELECT
            COUNT(*)::int                                                          AS total,
            (SUM(CASE WHEN outcome = 'tp' THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*), 0))                                             AS win_rate,
            AVG(pnl_pct)::float                                                    AS avg_pnl_pct,
            AVG(holding_seconds)::float                                            AS avg_holding_seconds
        FROM trade_tracking
        WHERE is_simulated = false
          AND outcome IS NOT NULL
          AND exit_time > NOW() - (:days || ' days')::interval
        """
    )
    agg = (await db.execute(agg_sql, {"days": days})).one()

    curve_sql = text(
        """
        SELECT
            exit_time AS time,
            SUM(pnl_pct) OVER (ORDER BY exit_time
                               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)::float
                AS cumulative_pnl_pct
        FROM trade_tracking
        WHERE is_simulated = false
          AND outcome IS NOT NULL
          AND exit_time > NOW() - (:days || ' days')::interval
        ORDER BY exit_time
        LIMIT 1000
        """
    )
    curve_rows = (await db.execute(curve_sql, {"days": days})).all()
    cumulative_pnl = [
        {
            "time": r.time.isoformat() if r.time else None,
            "cumulative_pnl_pct": float(r.cumulative_pnl_pct) if r.cumulative_pnl_pct is not None else 0.0,
        }
        for r in curve_rows
    ]

    return TradesAggResponse(
        window_days=days,
        total=int(agg.total or 0),
        win_rate=float(agg.win_rate) if agg.win_rate is not None else None,
        avg_pnl_pct=float(agg.avg_pnl_pct) if agg.avg_pnl_pct is not None else None,
        avg_holding_seconds=float(agg.avg_holding_seconds) if agg.avg_holding_seconds is not None else None,
        cumulative_pnl=cumulative_pnl,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. /trade-comparison
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/trade-comparison", response_model=TradeComparisonResponse)
async def get_trade_comparison(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
):
    sql = text(
        """
        SELECT
            is_simulated,
            COUNT(*)::int                                                       AS total,
            (SUM(CASE WHEN outcome = 'tp' THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*), 0))                                          AS win_rate,
            AVG(pnl_pct)::float                                                 AS avg_pnl_pct
        FROM trade_tracking
        WHERE outcome IS NOT NULL
          AND exit_time > NOW() - (:days || ' days')::interval
        GROUP BY is_simulated
        """
    )
    rows = (await db.execute(sql, {"days": days})).all()
    by_kind = {bool(r.is_simulated): r for r in rows}
    items: list[TradeComparisonItem] = []
    for is_sim, kind in ((False, "real"), (True, "simulated")):
        r = by_kind.get(is_sim)
        if r is None:
            items.append(TradeComparisonItem(
                kind=kind, total=0, win_rate=None, avg_pnl_pct=None,
            ))
        else:
            items.append(TradeComparisonItem(
                kind=kind,
                total=int(r.total or 0),
                win_rate=float(r.win_rate) if r.win_rate is not None else None,
                avg_pnl_pct=float(r.avg_pnl_pct) if r.avg_pnl_pct is not None else None,
            ))
    return TradeComparisonResponse(window_days=days, items=items)


# ─────────────────────────────────────────────────────────────────────────────
# 7. /ml-dataset
# ─────────────────────────────────────────────────────────────────────────────
_ML_PROJECTION = """
    id, symbol, direction, decision_type, result, time_to_result,
    entry_price, exit_price, timestamp_entry
"""


@router.get("/ml-dataset", response_model=MlDatasetResponse)
async def get_ml_dataset(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
):
    sql = text(
        f"""
        SELECT {_ML_PROJECTION}
        FROM trade_simulations
        ORDER BY timestamp_entry DESC
        LIMIT :limit
        """
    )
    rows = (await db.execute(sql, {"limit": limit})).all()
    items = [
        MlDatasetItem(
            id=str(r.id),
            symbol=r.symbol,
            direction=r.direction,
            decision_type=r.decision_type,
            result=r.result,
            time_to_result=r.time_to_result,
            entry_price=float(r.entry_price),
            exit_price=float(r.exit_price) if r.exit_price is not None else None,
            timestamp_entry=r.timestamp_entry,
        )
        for r in rows
    ]
    return MlDatasetResponse(total=len(items), items=items)


# ─────────────────────────────────────────────────────────────────────────────
# Task #225 — Operational observability
# ─────────────────────────────────────────────────────────────────────────────
# All endpoints below read from the OperationalSnapshotService cache.  They
# are O(1) and never touch Redis / Celery / DB synchronously — the background
# refreshers in services/operational_snapshot.py do that off-loop.
#
# `/overview` is the single endpoint the frontend banner uses (one HTTP call
# instead of six).  The per-family endpoints are kept for debugging and so
# operators can curl one snapshot at a time.
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/overview", response_model=OperationalOverviewResponse)
async def get_overview(_user_id: UUID = Depends(get_current_user_id)):
    """Aggregated operational snapshot — single source for the dashboard banner."""
    return get_ops_service().get_overview()


@router.get("/celery", response_model=SnapshotEnvelope)
async def get_celery_snapshot(_user_id: UUID = Depends(get_current_user_id)):
    return get_ops_service().celery.to_dict()


@router.get("/redis", response_model=SnapshotEnvelope)
async def get_redis_snapshot(_user_id: UUID = Depends(get_current_user_id)):
    return get_ops_service().redis.to_dict()


@router.get("/db-health", response_model=SnapshotEnvelope)
async def get_db_snapshot(_user_id: UUID = Depends(get_current_user_id)):
    return get_ops_service().db.to_dict()


@router.get("/score-engine", response_model=SnapshotEnvelope)
async def get_score_snapshot(_user_id: UUID = Depends(get_current_user_id)):
    return get_ops_service().score.to_dict()


@router.get("/pipeline-latency")
async def get_latency_snapshot(
    family: Optional[str] = Query(
        None,
        description="ingestion | decision | processing.  Omit to receive all three.",
    ),
    _user_id: UUID = Depends(get_current_user_id),
):
    """Pipeline latency split into three families.

    * ``ingestion``  — gap between NOW and the most recent OHLCV candle.
    * ``decision``   — p50/p95/max of ``decisions_log.latency_ms`` (1h).
    * ``processing`` — p50/p95 derived from the in-process Prometheus
                       ``indicator_computation_duration_seconds`` histogram.
    """
    svc = get_ops_service()
    families = {
        "ingestion":  svc.ingestion_latency.to_dict(),
        "decision":   svc.decision_latency.to_dict(),
        "processing": svc.processing_latency.to_dict(),
    }
    if family is None:
        return families
    if family not in families:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"unknown latency family: {family}")
    return families[family]


@router.get("/ingestion", response_model=SnapshotEnvelope)
async def get_ingestion_snapshot(_user_id: UUID = Depends(get_current_user_id)):
    return get_ops_service().ingestion.to_dict()


@router.get("/alerts")
async def get_alerts(_user_id: UUID = Depends(get_current_user_id)):
    return get_ops_service().get_alerts()


@router.get("/events")
async def get_events(
    category: Optional[str] = Query(
        None,
        description="alert | worker | redis | all.  Omit for all three buckets.",
    ),
    limit: int = Query(50, ge=1, le=100),
    _user_id: UUID = Depends(get_current_user_id),
):
    """Filtered event stream.

    Without ``category`` the response shape matches
    :class:`OperationalEventsResponse` (three buckets together).  When a
    category is supplied the response collapses to ``{events: [...]}``.
    """
    return get_ops_service().get_events(category=category, limit=limit)


@router.get("/ml-dataset/export")
async def export_ml_dataset(
    limit: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
):
    """CSV export — includes the full `features_snapshot` JSON for ML pipelines."""
    sql = text(
        """
        SELECT id, symbol, direction, decision_type, result, time_to_result,
               entry_price, exit_price, tp_price, sl_price,
               timestamp_entry, exit_timestamp, features_snapshot
        FROM trade_simulations
        ORDER BY timestamp_entry DESC
        LIMIT :limit
        """
    )
    rows = (await db.execute(sql, {"limit": limit})).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "symbol", "direction", "decision_type", "result", "time_to_result",
        "entry_price", "exit_price", "tp_price", "sl_price",
        "timestamp_entry", "exit_timestamp", "features_snapshot",
    ])
    import json as _json
    for r in rows:
        writer.writerow([
            str(r.id), r.symbol, r.direction, r.decision_type, r.result,
            r.time_to_result or "",
            float(r.entry_price),
            float(r.exit_price) if r.exit_price is not None else "",
            float(r.tp_price), float(r.sl_price),
            r.timestamp_entry.isoformat() if r.timestamp_entry else "",
            r.exit_timestamp.isoformat() if r.exit_timestamp else "",
            _json.dumps(r.features_snapshot) if r.features_snapshot else "",
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=scalpyn_ml_dataset.csv"},
    )
