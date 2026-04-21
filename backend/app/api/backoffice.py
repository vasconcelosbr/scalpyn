"""Backoffice API — Enterprise monitoring, decision logs, alerts, admin."""

import csv
import io
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.backoffice import DecisionLog, AssetTrace, BackofficeAlert, PipelineMetric
from ..models.user import User
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backoffice", tags=["Backoffice"])


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class AlertActionRequest(BaseModel):
    alert_id: UUID


class RoleUpdateRequest(BaseModel):
    role: str


class ReplayRequest(BaseModel):
    symbol: str
    strategy: str
    params: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_dt(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------------------
# 1. Pipeline Metrics
# ---------------------------------------------------------------------------

@router.get("/pipeline/metrics")
async def get_pipeline_metrics(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        offset = (page - 1) * per_page
        count_q = select(func.count(PipelineMetric.id))
        total = (await db.execute(count_q)).scalar() or 0

        q = (
            select(PipelineMetric)
            .order_by(desc(PipelineMetric.created_at))
            .offset(offset)
            .limit(per_page)
        )
        result = await db.execute(q)
        rows = result.scalars().all()

        return {
            "items": [
                {
                    "id": str(r.id),
                    "discovered": r.discovered,
                    "filtered": r.filtered,
                    "scored": r.scored,
                    "signals_count": r.signals_count,
                    "executed": r.executed,
                    "approved": r.approved,
                    "rejected": r.rejected,
                    "latency_ms": r.latency_ms,
                    "error_count": r.error_count,
                    "strategy": r.strategy,
                    "created_at": _serialize_dt(r.created_at),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching pipeline metrics: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch pipeline metrics")


@router.get("/pipeline/history")
async def get_pipeline_history(
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        q = (
            select(PipelineMetric)
            .where(PipelineMetric.created_at >= since)
            .order_by(PipelineMetric.created_at)
        )
        result = await db.execute(q)
        rows = result.scalars().all()

        return {
            "hours": hours,
            "data": [
                {
                    "timestamp": _serialize_dt(r.created_at),
                    "discovered": r.discovered,
                    "filtered": r.filtered,
                    "scored": r.scored,
                    "executed": r.executed,
                    "approved": r.approved,
                    "rejected": r.rejected,
                    "latency_ms": r.latency_ms,
                    "error_count": r.error_count,
                    "strategy": r.strategy,
                }
                for r in rows
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching pipeline history: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch pipeline history")


# ---------------------------------------------------------------------------
# 2. Asset Traces
# ---------------------------------------------------------------------------

@router.get("/assets")
async def get_asset_traces(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    symbol: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    score_min: Optional[float] = Query(None),
    score_max: Optional[float] = Query(None),
    strategy: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        conditions = []
        if symbol:
            conditions.append(AssetTrace.symbol == symbol.upper())
        if decision:
            conditions.append(AssetTrace.decision == decision)
        if score_min is not None:
            conditions.append(AssetTrace.score >= score_min)
        if score_max is not None:
            conditions.append(AssetTrace.score <= score_max)
        if strategy:
            conditions.append(AssetTrace.strategy == strategy)

        offset = (page - 1) * per_page
        count_q = select(func.count(AssetTrace.id))
        if conditions:
            count_q = count_q.where(and_(*conditions))
        total = (await db.execute(count_q)).scalar() or 0

        q = select(AssetTrace).order_by(desc(AssetTrace.created_at)).offset(offset).limit(per_page)
        if conditions:
            q = q.where(and_(*conditions))
        result = await db.execute(q)
        rows = result.scalars().all()

        return {
            "items": [
                {
                    "id": str(r.id),
                    "symbol": r.symbol,
                    "decision": r.decision,
                    "score": r.score,
                    "strategy": r.strategy,
                    "market_data_json": r.market_data_json,
                    "indicators_json": r.indicators_json,
                    "conditions_json": r.conditions_json,
                    "trace_id": r.trace_id,
                    "created_at": _serialize_dt(r.created_at),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching asset traces: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch asset traces")


@router.get("/assets/{symbol}")
async def get_asset_trace_by_symbol(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        q = (
            select(AssetTrace)
            .where(AssetTrace.symbol == symbol.upper())
            .order_by(desc(AssetTrace.created_at))
            .limit(1)
        )
        result = await db.execute(q)
        row = result.scalars().first()
        if not row:
            raise HTTPException(status_code=404, detail=f"No trace found for {symbol}")

        return {
            "id": str(row.id),
            "symbol": row.symbol,
            "decision": row.decision,
            "score": row.score,
            "strategy": row.strategy,
            "market_data_json": row.market_data_json,
            "indicators_json": row.indicators_json,
            "conditions_json": row.conditions_json,
            "trace_id": row.trace_id,
            "created_at": _serialize_dt(row.created_at),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching asset trace for %s: %s", symbol, e)
        raise HTTPException(status_code=500, detail="Failed to fetch asset trace")


# ---------------------------------------------------------------------------
# 3. Decision Log
# ---------------------------------------------------------------------------

def _build_decision_conditions(
    symbol: Optional[str],
    strategy: Optional[str],
    score_min: Optional[float],
    score_max: Optional[float],
    start_date: Optional[str],
    end_date: Optional[str],
    decision: Optional[str],
):
    conditions = []
    if symbol:
        conditions.append(DecisionLog.symbol == symbol.upper())
    if strategy:
        conditions.append(DecisionLog.strategy == strategy)
    if score_min is not None:
        conditions.append(DecisionLog.score >= score_min)
    if score_max is not None:
        conditions.append(DecisionLog.score <= score_max)
    if start_date:
        conditions.append(DecisionLog.created_at >= datetime.fromisoformat(start_date))
    if end_date:
        conditions.append(DecisionLog.created_at <= datetime.fromisoformat(end_date))
    if decision:
        conditions.append(DecisionLog.decision == decision)
    return conditions


@router.get("/decisions")
async def get_decisions(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    score_min: Optional[float] = Query(None),
    score_max: Optional[float] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        conditions = _build_decision_conditions(
            symbol, strategy, score_min, score_max, start_date, end_date, decision
        )

        offset = (page - 1) * per_page
        count_q = select(func.count(DecisionLog.id))
        if conditions:
            count_q = count_q.where(and_(*conditions))
        total = (await db.execute(count_q)).scalar() or 0

        q = select(DecisionLog).order_by(desc(DecisionLog.created_at)).offset(offset).limit(per_page)
        if conditions:
            q = q.where(and_(*conditions))
        result = await db.execute(q)
        rows = result.scalars().all()

        return {
            "items": [
                {
                    "id": str(r.id),
                    "symbol": r.symbol,
                    "strategy": r.strategy,
                    "timeframe": r.timeframe,
                    "score": r.score,
                    "decision": r.decision,
                    "l1_pass": r.l1_pass,
                    "l2_pass": r.l2_pass,
                    "l3_pass": r.l3_pass,
                    "reasons": r.reasons,
                    "metrics": r.metrics,
                    "latency_ms": r.latency_ms,
                    "created_at": _serialize_dt(r.created_at),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching decisions: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch decisions")


@router.post("/decisions/export")
async def export_decisions(
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    score_min: Optional[float] = Query(None),
    score_max: Optional[float] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        conditions = _build_decision_conditions(
            symbol, strategy, score_min, score_max, start_date, end_date, decision
        )

        q = select(DecisionLog).order_by(desc(DecisionLog.created_at))
        if conditions:
            q = q.where(and_(*conditions))
        result = await db.execute(q)
        rows = result.scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id",
            "symbol",
            "strategy",
            "timeframe",
            "score",
            "decision",
            "l1_pass",
            "l2_pass",
            "l3_pass",
            "latency_ms",
            "created_at",
        ])
        for r in rows:
            writer.writerow([
                str(r.id), r.symbol, r.strategy, r.timeframe, r.score,
                r.decision, r.l1_pass, r.l2_pass, r.l3_pass, r.latency_ms, _serialize_dt(r.created_at),
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=decisions_export.csv"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error exporting decisions: %s", e)
        raise HTTPException(status_code=500, detail="Failed to export decisions")


# ---------------------------------------------------------------------------
# 4. Alerts
# ---------------------------------------------------------------------------

@router.get("/alerts")
async def get_alerts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    alert_type: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        conditions = []
        if alert_type:
            conditions.append(BackofficeAlert.alert_type == alert_type)
        if status_filter:
            conditions.append(BackofficeAlert.status == status_filter)
        if category:
            conditions.append(BackofficeAlert.category == category)

        offset = (page - 1) * per_page
        count_q = select(func.count(BackofficeAlert.id))
        if conditions:
            count_q = count_q.where(and_(*conditions))
        total = (await db.execute(count_q)).scalar() or 0

        q = (
            select(BackofficeAlert)
            .order_by(desc(BackofficeAlert.created_at))
            .offset(offset)
            .limit(per_page)
        )
        if conditions:
            q = q.where(and_(*conditions))
        result = await db.execute(q)
        rows = result.scalars().all()

        return {
            "items": [
                {
                    "id": str(r.id),
                    "alert_type": r.alert_type,
                    "category": r.category,
                    "message": r.message,
                    "details_json": r.details_json,
                    "status": r.status,
                    "acknowledged_by": str(r.acknowledged_by) if r.acknowledged_by else None,
                    "acknowledged_at": _serialize_dt(r.acknowledged_at),
                    "resolved_at": _serialize_dt(r.resolved_at),
                    "created_at": _serialize_dt(r.created_at),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching alerts: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch alerts")


@router.post("/alerts/acknowledge")
async def acknowledge_alert(
    body: AlertActionRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        q = select(BackofficeAlert).where(BackofficeAlert.id == body.alert_id)
        result = await db.execute(q)
        alert = result.scalars().first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        alert.status = "acknowledged"
        alert.acknowledged_by = user_id
        alert.acknowledged_at = datetime.now(timezone.utc)
        await db.commit()

        return {"status": "acknowledged", "alert_id": str(body.alert_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error acknowledging alert: %s", e)
        raise HTTPException(status_code=500, detail="Failed to acknowledge alert")


@router.post("/alerts/resolve")
async def resolve_alert(
    body: AlertActionRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        q = select(BackofficeAlert).where(BackofficeAlert.id == body.alert_id)
        result = await db.execute(q)
        alert = result.scalars().first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        alert.status = "resolved"
        alert.resolved_at = datetime.now(timezone.utc)
        await db.commit()

        return {"status": "resolved", "alert_id": str(body.alert_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error resolving alert: %s", e)
        raise HTTPException(status_code=500, detail="Failed to resolve alert")


# ---------------------------------------------------------------------------
# 5. Data Integrity
# ---------------------------------------------------------------------------

@router.get("/data/integrity")
async def get_data_integrity(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        # Feed delay: seconds since last pipeline metric
        latest_metric_q = select(func.max(PipelineMetric.created_at))
        latest_metric_ts = (await db.execute(latest_metric_q)).scalar()
        feed_delay_seconds = 0.0
        if latest_metric_ts:
            ts = latest_metric_ts if latest_metric_ts.tzinfo else latest_metric_ts.replace(tzinfo=timezone.utc)
            feed_delay_seconds = round((datetime.now(timezone.utc) - ts).total_seconds(), 2)

        # Stale symbols: symbols whose latest asset_trace is older than 10 min
        stale_threshold = datetime.now(timezone.utc) - timedelta(minutes=10)
        subq = (
            select(
                AssetTrace.symbol,
                func.max(AssetTrace.created_at).label("last_seen"),
            )
            .group_by(AssetTrace.symbol)
        ).subquery()
        stale_q = select(func.count()).select_from(subq).where(subq.c.last_seen < stale_threshold)
        stale_symbols_count = (await db.execute(stale_q)).scalar() or 0

        # Avg pipeline latency
        avg_latency_q = select(func.avg(PipelineMetric.latency_ms)).where(
            PipelineMetric.created_at >= datetime.now(timezone.utc) - timedelta(hours=1)
        )
        avg_pipeline_latency_ms = (await db.execute(avg_latency_q)).scalar()
        avg_pipeline_latency_ms = round(avg_pipeline_latency_ms, 2) if avg_pipeline_latency_ms else 0.0

        return {
            "feed_delay_seconds": feed_delay_seconds,
            "stale_symbols_count": stale_symbols_count,
            "avg_pipeline_latency_ms": avg_pipeline_latency_ms,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching data integrity: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch data integrity")


# ---------------------------------------------------------------------------
# 6. Dashboard KPIs
# ---------------------------------------------------------------------------

@router.get("/dashboard")
async def get_dashboard_kpis(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        # Total assets analyzed (distinct symbols in asset_traces last 24h)
        total_assets_q = select(func.count(func.distinct(AssetTrace.symbol))).where(
            AssetTrace.created_at >= since
        )
        total_assets_analyzed = (await db.execute(total_assets_q)).scalar() or 0

        # Pipeline aggregates last 24h
        pipeline_q = select(
            func.sum(PipelineMetric.discovered).label("discovered"),
            func.sum(PipelineMetric.filtered).label("filtered"),
            func.sum(PipelineMetric.scored).label("scored"),
            func.sum(PipelineMetric.executed).label("executed"),
            func.sum(PipelineMetric.approved).label("approved"),
            func.sum(PipelineMetric.rejected).label("rejected"),
            func.sum(PipelineMetric.error_count).label("error_count"),
            func.avg(PipelineMetric.latency_ms).label("avg_latency_ms"),
        ).where(PipelineMetric.created_at >= since)
        pipeline_row = (await db.execute(pipeline_q)).first()

        approved = pipeline_row.approved or 0 if pipeline_row else 0
        rejected = pipeline_row.rejected or 0 if pipeline_row else 0
        total_decisions = approved + rejected
        approval_rate = round((approved / total_decisions * 100), 2) if total_decisions > 0 else 0.0

        avg_latency_ms = round(pipeline_row.avg_latency_ms, 2) if pipeline_row and pipeline_row.avg_latency_ms else 0.0

        total_executed = pipeline_row.executed or 0 if pipeline_row else 0
        error_count = pipeline_row.error_count or 0 if pipeline_row else 0
        error_rate = round((error_count / total_executed * 100), 2) if total_executed > 0 else 0.0

        recent_pipeline = {
            "discovered": pipeline_row.discovered or 0 if pipeline_row else 0,
            "filtered": pipeline_row.filtered or 0 if pipeline_row else 0,
            "scored": pipeline_row.scored or 0 if pipeline_row else 0,
            "executed": total_executed,
            "approved": approved,
            "rejected": rejected,
            "error_count": error_count,
        }

        # Strategy performance grouped by strategy
        strategy_q = (
            select(
                PipelineMetric.strategy,
                func.sum(PipelineMetric.approved).label("approved"),
                func.sum(PipelineMetric.rejected).label("rejected"),
                func.sum(PipelineMetric.executed).label("executed"),
                func.avg(PipelineMetric.latency_ms).label("avg_latency_ms"),
            )
            .where(PipelineMetric.created_at >= since)
            .group_by(PipelineMetric.strategy)
        )
        strategy_result = await db.execute(strategy_q)
        strategy_performance = [
            {
                "strategy": row.strategy,
                "approved": row.approved or 0,
                "rejected": row.rejected or 0,
                "executed": row.executed or 0,
                "avg_latency_ms": round(row.avg_latency_ms, 2) if row.avg_latency_ms else 0.0,
            }
            for row in strategy_result.all()
        ]

        return {
            "total_assets_analyzed": total_assets_analyzed,
            "approval_rate": approval_rate,
            "avg_latency_ms": avg_latency_ms,
            "error_rate": error_rate,
            "recent_pipeline": recent_pipeline,
            "strategy_performance": strategy_performance,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching dashboard KPIs: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch dashboard KPIs")


# ---------------------------------------------------------------------------
# 7. Replay (stub)
# ---------------------------------------------------------------------------

@router.post("/replay/run")
async def run_replay(
    body: ReplayRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return {
            "symbol": body.symbol,
            "strategy": body.strategy,
            "params": body.params,
            "result": {
                "original": {"score": 72.5, "decision": "approve", "latency_ms": 145.3},
                "replay": {"score": 75.1, "decision": "approve", "latency_ms": 138.7},
                "diff": {"score_delta": 2.6, "latency_delta": -6.6},
            },
            "status": "completed",
            "note": "Stub response — replay engine not yet implemented",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error running replay: %s", e)
        raise HTTPException(status_code=500, detail="Failed to run replay")


# ---------------------------------------------------------------------------
# 8. Admin RBAC
# ---------------------------------------------------------------------------

VALID_ROLES = {"admin", "operator", "viewer", "trader"}


@router.get("/admin/users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        # Verify current user is admin
        current_user_q = select(User).where(User.id == user_id)
        current_user = (await db.execute(current_user_q)).scalars().first()
        if not current_user or current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")

        q = select(User).order_by(User.created_at)
        result = await db.execute(q)
        users = result.scalars().all()

        return {
            "users": [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "name": u.name,
                    "role": u.role,
                    "is_active": u.is_active,
                    "created_at": _serialize_dt(u.created_at),
                }
                for u in users
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error listing users: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list users")


@router.put("/admin/users/{target_user_id}/role")
async def update_user_role(
    target_user_id: UUID,
    body: RoleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        # Verify current user is admin
        current_user_q = select(User).where(User.id == user_id)
        current_user = (await db.execute(current_user_q)).scalars().first()
        if not current_user or current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")

        if body.role not in VALID_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_ROLES))}",
            )

        target_q = select(User).where(User.id == target_user_id)
        target_user = (await db.execute(target_q)).scalars().first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        target_user.role = body.role
        await db.commit()

        return {
            "id": str(target_user.id),
            "email": target_user.email,
            "name": target_user.name,
            "role": target_user.role,
            "updated": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating user role: %s", e)
        raise HTTPException(status_code=500, detail="Failed to update user role")
