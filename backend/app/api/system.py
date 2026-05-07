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


# ── Per-queue alert thresholds (Task #216, operator spec part 5) ────────────
# Hysteresis avoids alert flapping: we go CRITICAL when depth crosses the
# upper bound and only re-arm (allow another CRITICAL) once depth drops
# back below the lower bound.
QUEUE_ALERT_HIGH = 10_000
QUEUE_ALERT_LOW = 8_000
QUEUE_ALERT_STATE_PREFIX = "scalpyn:celery_queue_alert_state:"
QUEUE_OLDEST_AGE_SAMPLES = 1  # peek at queue head only — cheap


def _peek_oldest_age_seconds(redis_client, queue_name: str) -> Optional[float]:
    """Return the wall-clock age of the oldest message in ``queue_name``.

    Celery messages are JSON envelopes whose ``properties.timestamp``
    (or, in newer kombu versions, ``headers.timestamp``) is the enqueue
    time. We LRANGE the head element and parse defensively — anything we
    cannot decode returns ``None`` rather than crashing the endpoint.
    """
    try:
        import json
        raw = redis_client.lrange(queue_name, 0, 0)
        if not raw:
            return None
        payload = raw[0]
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="replace")
        msg = json.loads(payload)
        # Probe multiple paths in priority order:
        #   1. ``headers.x-scalpyn-enqueued-at`` — stamped by our
        #      ``task_dispatch.enqueue()`` wrapper (Step 5). Always
        #      present for tasks that go through the wrapper, which is
        #      every periodic + chained dispatch from ``app/tasks/``.
        #   2. ``properties.timestamp`` / ``headers.timestamp`` — Celery's
        #      stock envelope. Best-effort: depends on the broker
        #      serializer.
        # Inspecting the head element only costs one LRANGE per queue
        # per status call and lets the operator dashboard surface a
        # real number (acceptance criterion C) without polling Celery
        # workers for individual task metadata.
        timestamp_str: Optional[str] = None
        for path in (
            ("headers", "x-scalpyn-enqueued-at"),
            ("properties", "timestamp"),
            ("headers", "timestamp"),
        ):
            cur: Any = msg
            for key in path:
                if not isinstance(cur, dict):
                    cur = None
                    break
                cur = cur.get(key)
            if cur:
                timestamp_str = str(cur)
                break
        if not timestamp_str:
            return None
        # Celery enqueue timestamps are ISO-8601 (UTC). datetime.fromisoformat
        # accepts both ``2026-05-04T12:34:56`` and ``...+00:00``; the
        # trailing ``Z`` form needs a manual swap.
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return None


async def _emit_backoffice_alert(queue_name: str, depth: int) -> None:
    """Persist a CRITICAL ``BackofficeAlert`` so the operator UI surfaces
    the queue backlog alongside other system alerts. Best-effort: an
    insert failure must never crash the status endpoint, so any error is
    swallowed after a WARNING log."""
    try:
        from ..models.backoffice import BackofficeAlert
        from ..database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            db.add(BackofficeAlert(
                alert_type="critical",
                category="celery_queue_backlog",
                message=(
                    f"Celery queue '{queue_name}' depth={depth} crossed "
                    f"threshold {QUEUE_ALERT_HIGH}. Workers may be stalled "
                    f"or undersized. Runbook: "
                    f"docs/runbooks/celery-queue-topology.md"
                ),
                details_json={
                    "queue": queue_name,
                    "depth": depth,
                    "threshold_high": QUEUE_ALERT_HIGH,
                    "threshold_low": QUEUE_ALERT_LOW,
                },
                status="active",
            ))
            await db.commit()
    except Exception as exc:
        logger.warning(
            "[celery-status] BackofficeAlert insert failed (queue=%s): %s",
            queue_name, exc,
        )


def _evaluate_queue_alert(redis_client, queue_name: str, depth: int) -> tuple[str, bool]:
    """Apply hysteresis and emit a CRITICAL log when threshold is crossed.

    Returns ``(state, alert_fired)`` — ``state`` is ``"ok"`` or
    ``"alerted"``, ``alert_fired`` is True only on the rising-edge
    crossing so the caller can persist a ``BackofficeAlert`` exactly
    once per cycle.
    """
    state_key = f"{QUEUE_ALERT_STATE_PREFIX}{queue_name}"
    try:
        prev_raw = redis_client.get(state_key)
        prev_state = (
            prev_raw.decode("utf-8", errors="replace")
            if isinstance(prev_raw, (bytes, bytearray))
            else (prev_raw or "ok")
        )
    except Exception:
        prev_state = "ok"

    new_state = prev_state
    alert_fired = False
    if depth >= QUEUE_ALERT_HIGH and prev_state != "alerted":
        # Cross the upper bound: emit a single CRITICAL line per cycle and
        # arm the latch so we do not re-alert until depth drops below LOW.
        logger.critical(
            "[celery-status] CRITICAL queue=%s depth=%d threshold=%d "
            "(workers may be stalled or undersized — see runbook "
            "docs/runbooks/celery-queue-topology.md)",
            queue_name, depth, QUEUE_ALERT_HIGH,
        )
        new_state = "alerted"
        alert_fired = True
    elif depth < QUEUE_ALERT_LOW and prev_state == "alerted":
        new_state = "ok"

    if new_state != prev_state:
        try:
            redis_client.set(state_key, new_state, ex=24 * 3600)
        except Exception:
            pass
    return new_state, alert_fired


@router.get("/persistence", include_in_schema=False)
async def persistence_status(authorization: Optional[str] = Header(None)):
    """Snapshot of the persistence queue / worker pool (Task #226).

    Bearer-gated (``DIAGNOSTICS_BEARER_TOKEN``) — same protection as
    ``/celery-diagnostics`` and ``/metrics``.  Even though the payload is
    only counts/depth, leaking queue saturation is a useful signal for an
    attacker probing for backpressure-induced DoS.
    """
    _require_diagnostics_bearer(authorization)
    from ..services.persistence import get_queue_snapshot
    snap = get_queue_snapshot()
    # Healthy when queue is well below capacity AND at least one worker is
    # alive when the feature flag is on.  When the flag is off we still
    # report status="ok" because workers are intentionally idle.
    depth = snap["depth_total"]
    maxsize = snap["maxsize"]
    workers = snap["workers_alive"]
    enabled = snap["enabled"]
    if enabled and workers == 0:
        status = "critical"
    elif depth >= int(maxsize * 0.9):
        status = "degraded"
    elif depth >= int(maxsize * 0.5):
        status = "warning"
    else:
        status = "ok"
    return {"status": status, **snap}


_CELERY_STATUS_CACHE: Dict[str, Any] = {"payload": None, "expires_at": 0.0}
_CELERY_STATUS_TTL_SECONDS = 10.0


@router.get("/celery-status")
async def get_celery_status():
    """Per-queue Celery worker + queue health (Task #216, operator spec).

    Reports per queue (``microstructure``, ``structural``, ``execution``):
        * ``depth``      — Redis LLEN
        * ``oldest_age_s`` — wall-clock age of the message at the head
        * ``alert_state`` — ``ok`` or ``alerted`` (hysteresis: trips at
          ``QUEUE_ALERT_HIGH``, re-arms below ``QUEUE_ALERT_LOW``)

    Plus aggregate worker info via Celery ``inspect``.

    HTTP 200 always; the dashboard renders ``alert_state`` red when any
    queue is ``alerted``. Intentionally unauthenticated so Cloud Run /
    Uptime Kuma / curl can probe without a JWT.

    Result is cached in-process for ``_CELERY_STATUS_TTL_SECONDS`` (10 s)
    to avoid hammering the broker with ``inspect.ping/active/registered``
    on every dashboard poll — those calls block on broker RTT and stack
    to ~3 s × 3 when no worker replies (e.g. degraded prod topology).
    """
    now_ts = _time.time()
    cached = _CELERY_STATUS_CACHE.get("payload")
    if cached is not None and _CELERY_STATUS_CACHE.get("expires_at", 0.0) > now_ts:
        return cached

    from ..tasks.celery_app import celery_app, ALL_QUEUES

    # Both Celery `inspect.*` and `redis-py` sync calls below are
    # blocking I/O. Running them directly on the event loop pins the
    # entire uvicorn worker for the duration (3 s timeout × 3 inspects
    # = up to 9 s, plus Redis Labs RTT × N queues). Worse: while the
    # loop is pinned, EVERY OTHER endpoint queues — that is what was
    # making `/api/dashboard/overview`, `/api/auth/login`, etc. hang
    # for minutes during the 17:15 incident. We isolate the whole sync
    # block in a dedicated thread so the event loop stays responsive.
    result: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "worker_alive": False,
        "worker_count": 0,
        "active_tasks": 0,
        "registered_task_count": 0,
        # Spec'd per-queue payloads (operator spec part 7). The
        # ``queues`` dict is the structured shape; the flat
        # ``queue_depth_by_queue`` / ``oldest_task_age_seconds_by_queue``
        # mirrors live alongside it for direct alert-rule consumption.
        "queues": {q: {"depth": None, "oldest_age_s": None, "alert_state": "unknown"}
                   for q in ALL_QUEUES},
        "queue_depth_by_queue": {q: None for q in ALL_QUEUES},
        "oldest_task_age_seconds_by_queue": {q: None for q in ALL_QUEUES},
        # Legacy single-queue scalar (Task #186). Kept for backward
        # compatibility with existing dashboards / alert rules — now
        # represents the SUM of the three per-queue depths.
        "queue_depth": None,
        "redis_error": None,
        "error": None,
    }

    def _probe_blocking() -> tuple[Dict[str, Any], list[tuple[str, int]]]:
        """All blocking I/O — runs inside ``asyncio.to_thread``.

        Returns the partially-filled result dict plus the list of
        rising-edge alerts to persist (BackofficeAlert insert is async,
        so it stays in the caller).
        """
        fired: list[tuple[str, int]] = []
        try:
            # 1 s is plenty for live workers (single-digit ms reply); a
            # missing worker contributes the full second once, then the
            # cache absorbs subsequent polls for 10 s.
            inspect = celery_app.control.inspect(timeout=1.0)
            ping = inspect.ping()
            if ping:
                result["worker_alive"] = True
                result["worker_count"] = len(ping)
                # Skip active/registered when ping is empty — both would
                # just stall until their own timeouts and return None.
                active = inspect.active()
                if active:
                    result["active_tasks"] = sum(len(v) for v in active.values())
                registered = inspect.registered()
                if registered:
                    result["registered_task_count"] = sum(
                        len(v) for v in registered.values()
                    )
        except Exception as exc:
            result["error"] = str(exc)
            logger.warning("[celery-status] inspect failed: %s", exc)

        try:
            import redis as _redis
            from ..config import settings
            # ``socket_timeout`` (operation) is what protects against a
            # half-open TCP / lazy Redis Labs reply — without it, an
            # ``LLEN``/``LRANGE`` can hang indefinitely. 2 s budget per
            # call keeps the total bounded even when probing 3 queues.
            r = _redis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            total_depth = 0
            any_depth_seen = False
            for queue_name in ALL_QUEUES:
                try:
                    depth = int(r.llen(queue_name))
                except Exception as exc:
                    logger.warning(
                        "[celery-status] LLEN failed for queue=%s: %s",
                        queue_name, exc,
                    )
                    continue
                oldest_age = (
                    _peek_oldest_age_seconds(r, queue_name) if depth else None
                )
                alert_state, alert_fired = _evaluate_queue_alert(
                    r, queue_name, depth
                )
                result["queues"][queue_name] = {
                    "depth": depth,
                    "oldest_age_s": oldest_age,
                    "alert_state": alert_state,
                }
                result["queue_depth_by_queue"][queue_name] = depth
                result["oldest_task_age_seconds_by_queue"][queue_name] = oldest_age
                total_depth += depth
                any_depth_seen = True
                if alert_fired:
                    fired.append((queue_name, depth))
            if any_depth_seen:
                result["queue_depth"] = total_depth
        except Exception as exc:
            result["redis_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("[celery-status] redis queue probe failed: %s", exc)
        return result, fired

    import asyncio as _asyncio
    try:
        # Hard upper bound on the whole sync block. If anything exceeds
        # this, we serve a degraded payload (defaults already populated
        # in ``result``) instead of dragging the request beyond the
        # Cloud Run 300 s frontend timeout.
        _, fired_alerts = await _asyncio.wait_for(
            _asyncio.to_thread(_probe_blocking),
            timeout=8.0,
        )
    except _asyncio.TimeoutError:
        result["error"] = "probe_timeout_8s"
        fired_alerts = []
        logger.warning("[celery-status] probe exceeded 8 s — serving degraded payload")

    # Persist BackofficeAlert rows for any rising-edge crossings — once
    # per crossing thanks to the hysteresis latch. Done after the Redis
    # loop so the response payload is complete even if the alert writer
    # hits an error. Stays on the event loop because it uses the async
    # SQLAlchemy session.
    for queue_name, depth in fired_alerts:
        await _emit_backoffice_alert(queue_name, depth)

    _CELERY_STATUS_CACHE["payload"] = result
    _CELERY_STATUS_CACHE["expires_at"] = _time.time() + _CELERY_STATUS_TTL_SECONDS
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
