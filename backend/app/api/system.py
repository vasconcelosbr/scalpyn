"""System health and monitoring API endpoints."""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..database import get_db
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["System"])


@router.get("/celery-status")
async def get_celery_status():
    """
    Probe Celery worker and beat health without authentication.

    Pings all active workers via Celery inspect and reports:
    - Whether any workers responded (worker_alive)
    - Active task count
    - Registered task count
    - Queue depth in Redis (tasks waiting to be consumed)

    Returns HTTP 200 always; check ``worker_alive`` to determine health.
    This endpoint is intentionally unauthenticated so it can be hit from
    Cloud Run health checks, Uptime Kuma, or a plain curl without a JWT.
    """
    from ..tasks.celery_app import celery_app

    result: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "worker_alive": False,
        "worker_count": 0,
        "active_tasks": 0,
        "registered_task_count": 0,
        "queue_depth": None,
        "error": None,
    }

    try:
        inspect = celery_app.control.inspect(timeout=3)
        ping = inspect.ping()
        if ping:
            result["worker_alive"] = True
            result["worker_count"] = len(ping)

        active = inspect.active()
        if active:
            result["active_tasks"] = sum(len(v) for v in active.values())

        registered = inspect.registered()
        if registered:
            result["registered_task_count"] = sum(len(v) for v in registered.values())

    except Exception as exc:
        result["error"] = str(exc)
        logger.warning("[celery-status] inspect failed: %s", exc)

    # Queue depth: count messages in the default 'celery' queue via Redis
    try:
        import redis as _redis
        from ..config import settings
        r = _redis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
        result["queue_depth"] = r.llen("celery")
    except Exception as exc:
        logger.warning("[celery-status] redis queue depth check failed: %s", exc)

    return result


@router.get("/pipeline-status")
async def get_pipeline_status(
    db: AsyncSession = Depends(get_db),
):
    """
    Get comprehensive pipeline health status.

    Returns end-to-end pipeline metrics:
    - Decision logs count (L3 approvals)
    - Simulation coverage
    - Last simulation time
    - Error flags and warnings

    This endpoint provides critical visibility into the
    L3 → decision_logs → simulation → trade_simulations → ML pipeline.
    """
    try:
        # Query decisions_log
        decisions_result = await db.execute(text("""
            SELECT
                COUNT(*) as total_decisions,
                COUNT(*) FILTER (WHERE decision = 'ALLOW') as allow_decisions,
                COUNT(*) FILTER (WHERE decision = 'BLOCK') as block_decisions,
                MAX(created_at) as last_decision_time,
                COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') as decisions_last_hour
            FROM decisions_log
        """))
        decisions_row = decisions_result.fetchone()

        # Query trade_simulations
        simulations_result = await db.execute(text("""
            SELECT
                COUNT(*) as total_simulations,
                COUNT(DISTINCT decision_id) as unique_decisions_simulated,
                MAX(created_at) as last_simulation_time,
                COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') as simulations_last_hour,
                COUNT(*) FILTER (WHERE result = 'WIN') as wins,
                COUNT(*) FILTER (WHERE result = 'LOSS') as losses,
                COUNT(*) FILTER (WHERE result = 'TIMEOUT') as timeouts
            FROM trade_simulations
        """))
        simulations_row = simulations_result.fetchone()

        # Calculate metrics
        now = datetime.now(timezone.utc)

        total_decisions = decisions_row.total_decisions or 0
        total_simulations = simulations_row.total_simulations or 0
        unique_decisions_simulated = simulations_row.unique_decisions_simulated or 0

        last_decision_time = decisions_row.last_decision_time
        last_simulation_time = simulations_row.last_simulation_time

        # Convert to timezone-aware if needed
        if last_decision_time and last_decision_time.tzinfo is None:
            last_decision_time = last_decision_time.replace(tzinfo=timezone.utc)
        if last_simulation_time and last_simulation_time.tzinfo is None:
            last_simulation_time = last_simulation_time.replace(tzinfo=timezone.utc)

        # Calculate lags
        decision_lag_minutes = None
        simulation_lag_minutes = None

        if last_decision_time:
            decision_lag_seconds = (now - last_decision_time).total_seconds()
            decision_lag_minutes = int(decision_lag_seconds / 60)

        if last_simulation_time:
            simulation_lag_seconds = (now - last_simulation_time).total_seconds()
            simulation_lag_minutes = int(simulation_lag_seconds / 60)

        # Coverage calculation
        coverage_pct = 0
        if total_decisions > 0:
            coverage_pct = round((unique_decisions_simulated / total_decisions) * 100, 2)

        # Error flags
        errors = []
        warnings = []

        # Check if decisions are being logged
        if total_decisions == 0:
            errors.append("NO_DECISIONS: decision_logs table is empty")
        elif decision_lag_minutes and decision_lag_minutes > 60:
            warnings.append(f"STALE_DECISIONS: Last decision logged {decision_lag_minutes} minutes ago")

        # Check if simulations are running
        if total_simulations == 0:
            errors.append("NO_SIMULATIONS: trade_simulations table is empty")
        elif simulation_lag_minutes and simulation_lag_minutes > 30:
            warnings.append(f"STALE_SIMULATIONS: Last simulation {simulation_lag_minutes} minutes ago")

        # Check simulation coverage
        if total_decisions > 100 and coverage_pct < 50:
            warnings.append(f"LOW_COVERAGE: Only {coverage_pct}% of decisions have simulations")

        # Check recent activity
        decisions_last_hour = decisions_row.decisions_last_hour or 0
        simulations_last_hour = simulations_row.simulations_last_hour or 0

        if decisions_last_hour > 0 and simulations_last_hour == 0:
            warnings.append("NO_RECENT_SIMULATIONS: Decisions logged but no simulations in last hour")

        # Determine overall status
        if errors:
            status = "error"
        elif warnings:
            status = "warning"
        elif total_decisions > 0 and total_simulations > 0 and coverage_pct > 80:
            status = "healthy"
        else:
            status = "degraded"

        return {
            "status": status,
            "timestamp": now.isoformat(),
            "pipeline": {
                "decisions": {
                    "total": total_decisions,
                    "allow": decisions_row.allow_decisions or 0,
                    "block": decisions_row.block_decisions or 0,
                    "last_time": last_decision_time.isoformat() if last_decision_time else None,
                    "lag_minutes": decision_lag_minutes,
                    "last_hour": decisions_last_hour,
                },
                "simulations": {
                    "total": total_simulations,
                    "unique_decisions": unique_decisions_simulated,
                    "last_time": last_simulation_time.isoformat() if last_simulation_time else None,
                    "lag_minutes": simulation_lag_minutes,
                    "last_hour": simulations_last_hour,
                    "wins": simulations_row.wins or 0,
                    "losses": simulations_row.losses or 0,
                    "timeouts": simulations_row.timeouts or 0,
                },
                "coverage": {
                    "percentage": coverage_pct,
                    "simulated": unique_decisions_simulated,
                    "total_decisions": total_decisions,
                },
            },
            "errors": errors,
            "warnings": warnings,
            "health_summary": {
                "pipeline_operational": status in ("healthy", "warning"),
                "decisions_flowing": decisions_last_hour > 0,
                "simulations_running": simulations_last_hour > 0,
                "data_quality": "good" if not warnings else "degraded" if not errors else "poor",
            }
        }

    except Exception as exc:
        logger.error("Failed to get pipeline status: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get pipeline status: {str(exc)}"
        ) from exc
