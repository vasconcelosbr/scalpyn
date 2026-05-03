"""System health and monitoring API endpoints."""

import hmac
import logging
import os
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..database import get_db
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["System"])


# ── Celery diagnostics endpoint (Task #186) ──────────────────────────────────
# Bearer-token-gated SRE channel that exposes in-process Celery + Redis
# health: process inventory via psutil, ``inspect.active/registered/stats``,
# Redis ``ping``/``dbsize``, and the ``scalpyn:last_collect_all_*`` markers
# written by the instrumented ``collect_all`` task. With ``?dispatch=collect_all``
# the endpoint enqueues an on-demand run and reports its state 3 s later, so
# operators without ``gcloud`` access can still answer the 5 SRE questions
# (Redis? Worker? Beat? Task fires? Exact error?). Same access model as
# ``/metrics``: 404 when ``DIAGNOSTICS_BEARER_TOKEN`` is unset, 401 on a bad
# header. The token never appears in any response body or log line.

_BEARER_PREFIX = "Bearer "
LAST_COLLECT_ALL_START_KEY = "scalpyn:last_collect_all_start"
LAST_COLLECT_ALL_END_KEY = "scalpyn:last_collect_all_end"
COLLECT_ALL_RUNS_KEY = "scalpyn:collect_all_runs"
COLLECT_ALL_ERRORS_KEY = "scalpyn:collect_all_errors"
LAST_COLLECT_ALL_ERROR_KEY = "scalpyn:last_collect_all_error"


def _expected_diagnostics_token() -> Optional[str]:
    token = os.environ.get("DIAGNOSTICS_BEARER_TOKEN", "").strip()
    return token or None


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        return None
    return authorization[len(_BEARER_PREFIX):].strip() or None


def _require_diagnostics_bearer(authorization: Optional[str]) -> None:
    expected = _expected_diagnostics_token()
    if expected is None:
        # Endpoint hidden when the gate is not configured.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    presented = _extract_bearer(authorization)
    if presented is None or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _scan_celery_processes() -> Dict[str, Any]:
    """Inventory live Celery worker/beat processes via psutil.

    Returns lists of PIDs (no full cmdlines, no env vars — both could leak
    the broker password embedded in ``REDIS_URL``).
    """
    workers: list[int] = []
    beats: list[int] = []
    error: Optional[str] = None
    try:
        import psutil  # type: ignore
    except Exception as exc:
        return {
            "worker_processes": [],
            "beat_processes": [],
            "process_scan_error": f"psutil_unavailable: {type(exc).__name__}: {exc}",
        }
    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
            except Exception:
                continue
            joined = " ".join(cmdline)
            if "celery" not in joined:
                continue
            if " beat" in joined or joined.endswith(" beat") or "celery beat" in joined:
                beats.append(int(proc.info["pid"]))
            elif " worker" in joined or "celery worker" in joined:
                workers.append(int(proc.info["pid"]))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {
        "worker_processes": sorted(workers),
        "beat_processes": sorted(beats),
        "process_scan_error": error,
    }


def _redis_probe() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "redis_ping": False,
        "redis_dbsize": None,
        "last_collect_all_start": None,
        "last_collect_all_end": None,
        "collect_all_runs": None,
        "collect_all_errors": None,
        "last_collect_all_error": None,
        "redis_error": None,
    }
    # Numeric counters get explicit int casting so the runbook can treat
    # them as metrics; timestamp/error markers stay as strings.
    int_keys = {COLLECT_ALL_RUNS_KEY, COLLECT_ALL_ERRORS_KEY}
    try:
        import redis as _redis
        from ..config import settings
        r = _redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        out["redis_ping"] = bool(r.ping())
        out["redis_dbsize"] = int(r.dbsize())
        for key, dest in (
            (LAST_COLLECT_ALL_START_KEY, "last_collect_all_start"),
            (LAST_COLLECT_ALL_END_KEY, "last_collect_all_end"),
            (COLLECT_ALL_RUNS_KEY, "collect_all_runs"),
            (COLLECT_ALL_ERRORS_KEY, "collect_all_errors"),
            (LAST_COLLECT_ALL_ERROR_KEY, "last_collect_all_error"),
        ):
            try:
                val = r.get(key)
                if val is None:
                    continue
                if isinstance(val, (bytes, bytearray)):
                    val = val.decode("utf-8", errors="replace")
                if key in int_keys:
                    try:
                        val = int(val)
                    except (TypeError, ValueError):
                        pass
                out[dest] = val
            except Exception:
                continue
    except Exception as exc:
        out["redis_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _inspect_celery() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "inspect_active": None,
        "inspect_registered": None,
        "inspect_stats": None,
        "inspect_error": None,
    }
    try:
        from ..tasks.celery_app import celery_app
        insp = celery_app.control.inspect(timeout=2.0)
        out["inspect_active"] = insp.active() or {}
        registered = insp.registered() or {}
        # Deduplicate task names across workers for a flat list.
        flat: set[str] = set()
        for names in registered.values():
            for n in names or []:
                flat.add(n)
        out["inspect_registered"] = sorted(flat)
        stats = insp.stats() or {}
        # Drop ``broker.url`` style fields that may carry credentials.
        scrubbed: Dict[str, Any] = {}
        for node, payload in stats.items():
            if isinstance(payload, dict):
                payload = {
                    k: v for k, v in payload.items()
                    if "url" not in k.lower() and "password" not in k.lower()
                }
            scrubbed[node] = payload
        out["inspect_stats"] = scrubbed
    except Exception as exc:
        out["inspect_error"] = f"{type(exc).__name__}: {exc}"
    return out


@router.get("/celery-diagnostics", include_in_schema=False)
async def celery_diagnostics(
    authorization: Optional[str] = Header(default=None),
    dispatch: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """Bearer-gated Celery + Redis runtime probe (Task #186).

    Returns a flat JSON snapshot answering the five SRE questions
    (Redis? Worker? Beat? Task dispatch? Exact error?). With
    ``?dispatch=collect_all`` it enqueues an on-demand ``collect_all``
    run and waits 3 s for a state transition. Never echoes ``REDIS_URL``
    or any secret value.
    """
    _require_diagnostics_bearer(authorization)

    payload: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(_redis_probe())
    payload.update(_scan_celery_processes())
    payload.update(_inspect_celery())

    if dispatch:
        if dispatch != "collect_all":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only ?dispatch=collect_all is supported.",
            )
        dispatch_result: Dict[str, Any] = {
            "task": "app.tasks.collect_market_data.collect_all",
            "dispatched_task_id": None,
            "state_after_3s": None,
            "traceback": None,
            "error": None,
        }
        try:
            # Use the task's own ``apply_async()`` for spec fidelity (the
            # task spec calls out ``collect_all.apply_async()`` explicitly)
            # and for clearer traceability in Flower / inspect output.
            from ..tasks.collect_market_data import collect_all as _collect_all_task
            async_result = _collect_all_task.apply_async()
            dispatch_result["dispatched_task_id"] = async_result.id
            # Block 3 s in a thread so the event loop is not pinned.
            import asyncio as _asyncio

            def _poll() -> tuple[str, Optional[str]]:
                _time.sleep(3)
                state = async_result.state
                tb = None
                try:
                    if state in ("FAILURE",):
                        tb = str(async_result.traceback)
                except Exception:
                    tb = None
                return state, tb

            state, tb = await _asyncio.to_thread(_poll)
            dispatch_result["state_after_3s"] = state
            dispatch_result["traceback"] = tb
        except Exception as exc:
            dispatch_result["error"] = f"{type(exc).__name__}: {exc}"
        payload["dispatch"] = dispatch_result

    return payload


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
