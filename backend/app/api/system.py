"""System health and monitoring API endpoints."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..database import get_db
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["System"])


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


def _inspect_celery_sync(timeout: float = 3.0) -> dict:
    """
    Run Celery inspect calls synchronously (called via asyncio.to_thread).

    Returns a dict with worker ping, active tasks, scheduled tasks and
    a raw Redis ping — the same information that 'gcloud logging read'
    etapas 3-7 would surface, but available via a single HTTP call.
    """
    from ..tasks.celery_app import celery_app
    from ..config import settings
    import redis as redis_lib

    result: dict = {
        "redis": {"ok": False, "error": None, "url_host": None},
        "worker": {"ok": False, "names": [], "active_tasks": [], "error": None},
        "beat": {"ok": False, "scheduled_tasks": [], "error": None},
        "inspect_timeout_s": timeout,
    }

    # ── Redis ping ────────────────────────────────────────────────────────────
    try:
        import urllib.parse as _up
        parsed = _up.urlparse(settings.REDIS_URL)
        result["redis"]["url_host"] = f"{parsed.hostname}:{parsed.port or 6379}/db{parsed.path.lstrip('/')}"
        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=int(timeout))
        result["redis"]["ok"] = r.ping()
    except Exception as exc:
        result["redis"]["error"] = str(exc)

    # ── Celery inspect ────────────────────────────────────────────────────────
    try:
        insp = celery_app.control.inspect(timeout=timeout)

        # Worker ping: returns {worker_name: [{"ok": "pong"}]} or None
        ping_resp = insp.ping()
        if ping_resp:
            result["worker"]["ok"] = True
            result["worker"]["names"] = list(ping_resp.keys())
        else:
            result["worker"]["error"] = "No workers replied to ping (timeout or no workers running)"

        # Active tasks on all workers
        active_resp = insp.active()
        if active_resp:
            for worker_name, tasks in active_resp.items():
                for t in (tasks or []):
                    result["worker"]["active_tasks"].append({
                        "worker": worker_name,
                        "task": t.get("name"),
                        "id": t.get("id"),
                    })
    except Exception as exc:
        result["worker"]["error"] = str(exc)

    # ── Beat scheduled tasks (Celery beat heartbeat via inspect) ─────────────
    try:
        insp2 = celery_app.control.inspect(timeout=timeout)
        sched_resp = insp2.scheduled()
        if sched_resp is not None:
            result["beat"]["ok"] = True
            for worker_name, entries in sched_resp.items():
                for entry in (entries or []):
                    result["beat"]["scheduled_tasks"].append({
                        "worker": worker_name,
                        "eta": entry.get("eta"),
                        "task": entry.get("request", {}).get("name") if entry.get("request") else entry.get("name"),
                    })
        else:
            result["beat"]["error"] = "No scheduled tasks returned (beat may not be running or no tasks queued yet)"
    except Exception as exc:
        result["beat"]["error"] = str(exc)

    return result


@router.get("/celery-status")
async def get_celery_status(
    db: AsyncSession = Depends(get_db),
):
    """
    Inspect Celery worker, beat and Redis without gcloud.

    Covers etapas 3-7 do runbook SRE (substitui gcloud logging read +
    gcloud run services describe) — disponível via curl público.

    Returns:
    - redis: connectivity ping, host masked
    - worker: alive (ping), names, active tasks
    - beat: alive (scheduled tasks present), task list
    - ohlcv: MAX(time) from DB — prova se houve inserts recentes
    - pool_symbols: COUNT active pool_coins — prova universo não vazio
    - decisions_last_hour: replicado do /pipeline-status para correlação
    - summary: diagnóstico em linguagem natural
    """
    now = datetime.now(timezone.utc)

    # ── DB queries (async, no SQL restriction — read-only selects) ────────────
    try:
        ohlcv_row = (await db.execute(text(
            "SELECT MAX(time) AS last_time FROM ohlcv WHERE market_type = 'spot'"
        ))).fetchone()
        last_ohlcv = ohlcv_row.last_time if ohlcv_row else None
        if last_ohlcv and last_ohlcv.tzinfo is None:
            last_ohlcv = last_ohlcv.replace(tzinfo=timezone.utc)
        ohlcv_lag_min = int((now - last_ohlcv).total_seconds() / 60) if last_ohlcv else None
    except Exception as exc:
        last_ohlcv = None
        ohlcv_lag_min = None
        logger.warning("celery-status: ohlcv query failed: %s", exc)

    try:
        pool_row = (await db.execute(text(
            "SELECT COUNT(*) AS cnt FROM pool_coins WHERE is_active = true"
        ))).fetchone()
        pool_count = pool_row.cnt if pool_row else 0
    except Exception as exc:
        pool_count = None
        logger.warning("celery-status: pool_coins query failed: %s", exc)

    try:
        dec_row = (await db.execute(text(
            "SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') AS last_hour "
            "FROM decisions_log"
        ))).fetchone()
        decisions_last_hour = dec_row.last_hour if dec_row else 0
    except Exception as exc:
        decisions_last_hour = None
        logger.warning("celery-status: decisions_log query failed: %s", exc)

    # ── Celery + Redis inspect (sync, run in thread) ──────────────────────────
    try:
        celery_data = await asyncio.to_thread(_inspect_celery_sync, 4.0)
    except Exception as exc:
        celery_data = {
            "redis": {"ok": False, "error": str(exc)},
            "worker": {"ok": False, "error": str(exc)},
            "beat": {"ok": False, "error": str(exc)},
        }

    # ── Summary diagnosis ─────────────────────────────────────────────────────
    diagnoses = []
    root_cause = None

    redis_ok = celery_data["redis"]["ok"]
    worker_ok = celery_data["worker"]["ok"]
    beat_ok = celery_data["beat"]["ok"]
    ohlcv_stale = ohlcv_lag_min is not None and ohlcv_lag_min > 10

    if not redis_ok:
        root_cause = "REDIS_DOWN"
        diagnoses.append(f"Redis inacessível ({celery_data['redis'].get('error', 'no ping response')}) "
                         "— Celery não consegue receber tasks. Verificar REDIS_URL e Memorystore.")
    elif not worker_ok:
        root_cause = "WORKER_DOWN"
        diagnoses.append("Redis OK mas worker não respondeu ao ping — processo celery worker "
                         "morreu e watchdog não reiniciou o container. "
                         "Ação: forçar redeploy (push com FORCE_RESTART commitado).")
    elif not beat_ok:
        root_cause = "BEAT_DOWN"
        diagnoses.append("Redis OK, worker OK mas beat sem tasks agendadas — "
                         "processo celery beat morreu silenciosamente. "
                         "Ação: forçar redeploy.")
    elif ohlcv_stale:
        root_cause = "COLLECT_STALLED"
        diagnoses.append(f"Worker e beat ativos, Redis OK, mas ohlcv.max_time defasado {ohlcv_lag_min} min "
                         "— collect_all está sendo agendado mas falhando silenciosamente "
                         "(checar pool_coins vazio ou Gate.io rate-limit).")
    elif pool_count == 0:
        root_cause = "EMPTY_POOL"
        diagnoses.append("pool_coins está vazio — get_pool_symbols retorna [] e collect_all "
                         "encerra sem inserts.")
    else:
        diagnoses.append("Todos os componentes parecem saudáveis — pipeline deve estar operacional.")

    return {
        "timestamp": now.isoformat(),
        "etapa3_logs_coleta": {
            "ohlcv_last_insert": last_ohlcv.isoformat() if last_ohlcv else None,
            "ohlcv_lag_minutes": ohlcv_lag_min,
            "ohlcv_stale": ohlcv_stale,
        },
        "etapa4_celery_worker": {
            "ok": worker_ok,
            "worker_names": celery_data["worker"].get("names", []),
            "active_tasks": celery_data["worker"].get("active_tasks", []),
            "error": celery_data["worker"].get("error"),
        },
        "etapa5_beat": {
            "ok": beat_ok,
            "scheduled_count": len(celery_data["beat"].get("scheduled_tasks", [])),
            "scheduled_tasks": celery_data["beat"].get("scheduled_tasks", []),
            "error": celery_data["beat"].get("error"),
        },
        "etapa6_redis": {
            "ok": redis_ok,
            "host": celery_data["redis"].get("url_host"),
            "error": celery_data["redis"].get("error"),
        },
        "etapa7_watchdog": {
            "note": "Watchdog log lines ('WARNING: Celery process down') só visíveis via Cloud Logging. "
                    "Se worker_ok=false aqui, watchdog falhou em matar o container.",
        },
        "etapa8_pool_symbols": {
            "active_pool_coins": pool_count,
            "empty": pool_count == 0 if pool_count is not None else None,
        },
        "decisions_last_hour": decisions_last_hour,
        "root_cause": root_cause,
        "diagnosis": diagnoses,
        "action_required": root_cause is not None,
    }
