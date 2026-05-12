"""Scalpyn API — main FastAPI application."""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .init_db import init_db

from .api import (
    auth,
    config as config_api,
    pools,
    exchanges,
    decisions,
    market,
    trades,
    orders,
    analytics,
    reports,
    backoffice,
    notifications,
    watchlist,
    watchlists,
    websocket,
    profiles,
    custom_watchlists,
    ai_keys,
    ai_skills,
    asset_search,
    debug_indicators,
    simulations,
    ml,
    spot_engine,
    futures_engine,
    system,
    metrics as metrics_api,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    _log = logging.getLogger(__name__)

    # Schema bootstrap is owned by start.sh in production via `alembic upgrade
    # head` (the only gate).  Migration 021 mirrors init_db.py 1:1, so Alembic
    # alone converges the schema.  In dev, the workflow command runs uvicorn
    # directly without start.sh, so we still call init_db() here as a
    # convenience for fresh local DBs.  Production sets SKIP_LIFESPAN_INIT_DB=1
    # to keep this path inert and avoid lock contention during deploy.
    # The /api/health/schema endpoint below queries information_schema
    # directly and does not depend on this running.
    import os
    if os.environ.get("SKIP_LIFESPAN_INIT_DB") != "1":
        try:
            _log.info("Initializing database schema (dev convenience)...")
            await asyncio.wait_for(init_db(), timeout=30)
        except asyncio.TimeoutError:
            _log.warning("Database initialization timed out after 30 s — startup continues; production must rely on start.sh gates")
        except Exception as e:
            _log.error("Database initialization error: %s", e, exc_info=True)

    try:
        from sqlalchemy import text
        from .database import AsyncSessionLocal
        async with AsyncSessionLocal() as _sess:
            await asyncio.wait_for(_sess.execute(text("SELECT 1")), timeout=10)
        _log.info("DB connection pool warmed up successfully.")
    except asyncio.TimeoutError:
        _log.warning("DB warmup timed out after 10 s — will retry on first request")
    except Exception as e:
        _log.warning("DB warmup failed (will retry on first request): %s", e)

    # ── DB pool stats logger (Task #116) ──────────────────────────────────
    # Periodically logs pool utilisation so we can diagnose `QueuePool limit
    # … reached` errors.  Disable via DB_POOL_STATS_INTERVAL_SECONDS=0.
    pool_stats_task = None
    try:
        from .database import start_pool_stats_logger
        pool_stats_task = start_pool_stats_logger()
    except Exception as e:
        _log.warning("Pool stats logger failed to start: %s", e)
        pool_stats_task = None

    # ── Structural indicator scheduler (15 min, 1h OHLCV) ────────────────
    # Computes slow technical indicators: RSI, ADX, EMA, ATR, MACD, Bollinger,
    # PSAR, Z-score, OBV, Stochastic.  Disable via SKIP_STRUCTURAL_SCHEDULER=1.
    stop_structural_scheduler = None
    try:
        from .services.structural_scheduler_service import (
            start_structural_scheduler,
            stop_structural_scheduler,
        )
        start_structural_scheduler()
    except Exception as e:
        _log.warning("Structural scheduler failed to start: %s", e)
        stop_structural_scheduler = None  # type: ignore[assignment]

    # ── Microstructure indicator scheduler (5 min, 5m OHLCV + live data) ─
    # Computes fast indicators: VWAP, volume_spike, taker_ratio, volume_delta,
    # spread_pct, orderbook_depth.  Disable via SKIP_MICROSTRUCTURE_SCHEDULER=1.
    stop_microstructure_scheduler = None
    try:
        from .services.microstructure_scheduler_service import (
            start_microstructure_scheduler,
            stop_microstructure_scheduler,
        )
        start_microstructure_scheduler()
    except Exception as e:
        _log.warning("Microstructure scheduler failed to start: %s", e)
        stop_microstructure_scheduler = None  # type: ignore[assignment]

    # ── Combined scheduler (legacy, opt-in via ENABLE_COMBINED_SCHEDULER=1) ─
    # Disabled by default; structural + microstructure schedulers take over.
    stop_background_scheduler = None
    try:
        from .services.scheduler_service import (
            start_background_scheduler,
            stop_background_scheduler,
        )
        start_background_scheduler()
    except Exception as e:
        _log.warning("Combined scheduler failed to start: %s", e)
        stop_background_scheduler = None  # type: ignore[assignment]

    # ── Pipeline scan scheduler ───────────────────────────────────────────
    # Periodically runs the full pipeline scan (POOL → L1 → L2 → L3) so
    # `pipeline_watchlist_assets.refreshed_at`, `pipeline_watchlist_rejections`
    # and `pipeline_watchlist.last_scanned_at` stay populated even without a
    # Celery worker. Disable by setting SKIP_PIPELINE_SCHEDULER=1.
    stop_pipeline_scheduler = None
    try:
        from .services.pipeline_scheduler_service import (
            start_pipeline_scheduler,
            stop_pipeline_scheduler,
        )
        start_pipeline_scheduler()
    except Exception as e:
        _log.warning("Pipeline scheduler failed to start: %s", e)
        stop_pipeline_scheduler = None  # type: ignore[assignment]

    # ── Decision event subscriber (Redis→WebSocket IPC bridge) ───────────
    # Celery workers publish decision events to a Redis pub/sub channel;
    # this background task subscribes and forwards them to connected browsers
    # via ConnectionManager.broadcast(). Without this bridge, real-time
    # updates on the /decisions page are silently dropped because the
    # ConnectionManager singleton in the Celery process has no WS connections.
    # Failure to start is non-fatal — the REST polling fallback still works.
    decision_subscriber_task = None
    try:
        from .services.realtime_bridge import start_decision_event_subscriber
        decision_subscriber_task = await start_decision_event_subscriber()
    except Exception as e:
        _log.warning("Decision event subscriber failed to start: %s", e)
        decision_subscriber_task = None

    # ── Gate.io WebSocket — real-time order flow ingestion (Task #171) ────
    # Behind ENABLE_GATE_WS=1.  In multi-instance deployments only the
    # replica that wins the Redis ``gate_ws:leader`` lock opens the WS;
    # the rest stay as readers of the Redis trade buffer.  Failure to
    # acquire the lock or to start the WS is logged but never aborts boot
    # (REST polling continues to feed order flow as before).
    stop_gate_ws_leader = None
    try:
        from .services.gate_ws_leader import start_gate_ws_with_leader_election
        stop_gate_ws_leader = await start_gate_ws_with_leader_election()
    except Exception as e:
        _log.warning("Gate WS leader election failed to start: %s", e)
        stop_gate_ws_leader = None

    # ── Persistence queue + worker pool (Task #226) ────────────────────────
    # Long-running persistence workers consume messages from the bounded
    # PersistenceQueue and execute one short transaction per message.  When
    # USE_PERSISTENCE_QUEUE != "1" the workers still start (cheap, idle) so
    # producers that opt into the new path during a single deploy never
    # silently lose writes — the queue is always drained.
    try:
        from .services.persistence import start_workers as _start_persistence
        _start_persistence()
    except Exception as e:
        _log.warning("Persistence workers failed to start: %s", e)

    # ── Operational snapshot service (Task #225) ───────────────────────────
    # Keeps eventually-consistent snapshots of Celery / Redis / DB / score
    # engine / latency so /api/dashboard/overview can answer in O(1).  The
    # service swallows its own probe failures — if it cannot start at all
    # the dashboard degrades gracefully (snapshots stay status=unknown).
    stop_ops_snapshot = None
    try:
        from .services.operational_snapshot import start_operational_snapshot
        stop_ops_snapshot = await start_operational_snapshot()
    except Exception as e:
        _log.warning("Operational snapshot service failed to start: %s", e)
        stop_ops_snapshot = None

    # ── Consolidated scheduler startup summary (env-derived intervals) ──────
    _struct_interval   = int(os.environ.get("STRUCTURAL_SCHEDULER_INTERVAL_SECONDS", 900))
    _micro_interval    = int(os.environ.get("MICROSTRUCTURE_SCHEDULER_INTERVAL_SECONDS", 300))
    _pipeline_interval = int(os.environ.get("PIPELINE_SCHEDULER_INTERVAL_SECONDS", 600))
    _sched_summary = []
    if os.environ.get("ENABLE_COMBINED_SCHEDULER") == "1":
        _sched_summary.append(f"combined(legacy,{_pipeline_interval}s)")
    else:
        if os.environ.get("SKIP_STRUCTURAL_SCHEDULER") != "1":
            _sched_summary.append(f"[STRUCT-SCHED]({_struct_interval}s,1h-OHLCV)")
        else:
            _sched_summary.append("[STRUCT-SCHED](disabled)")
        if os.environ.get("SKIP_MICROSTRUCTURE_SCHEDULER") != "1":
            _sched_summary.append(f"[MICRO-SCHED]({_micro_interval}s,5m-OHLCV+live)")
        else:
            _sched_summary.append("[MICRO-SCHED](disabled)")
    if os.environ.get("SKIP_PIPELINE_SCHEDULER") != "1":
        _sched_summary.append(f"pipeline({_pipeline_interval}s)")
    _log.info("[SCHED-STARTUP] active schedulers: %s", " | ".join(_sched_summary))

    try:
        yield
    finally:
        # Cancel the decision event subscriber background task
        if decision_subscriber_task is not None:
            decision_subscriber_task.cancel()
            try:
                await decision_subscriber_task
            except (asyncio.CancelledError, Exception):
                pass

        # Order matters here:
        #   1. Stop schedulers / WS leader / ops snapshot FIRST so no new
        #      messages get enqueued.  If we stopped persistence workers
        #      first, any scheduler still running would call enqueue() on
        #      a closed queue and hit the `shutdown` drop path — silent
        #      data loss for the very last cycle (Task #226 review fix).
        #   2. Drain + stop persistence workers AFTER producers are quiet.
        for _stop_fn, _name in [
            (stop_structural_scheduler, "Structural scheduler"),
            (stop_microstructure_scheduler, "Microstructure scheduler"),
            (stop_background_scheduler, "Combined scheduler"),
            (stop_pipeline_scheduler, "Pipeline scheduler"),
            (stop_gate_ws_leader, "Gate WS leader"),
            (stop_ops_snapshot, "Operational snapshot service"),
        ]:
            if _stop_fn is not None:
                try:
                    await _stop_fn()
                except Exception as e:
                    _log.warning("%s shutdown error: %s", _name, e)

        # Producers are quiet — drain and stop persistence workers.  The
        # 10 s drain budget covers any in-flight enqueue from the schedulers
        # above; the queue stops accepting new puts after close().
        try:
            from .services.persistence import stop_workers as _stop_persistence
            await _stop_persistence(timeout=10.0)
        except Exception as e:
            _log.warning("Persistence workers shutdown error: %s", e)

        if pool_stats_task is not None:
            pool_stats_task.cancel()
            try:
                await pool_stats_task
            except (asyncio.CancelledError, Exception):
                pass
    return


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Scalpyn API — Institutional-Grade Quant Crypto Trading Platform",
    version="0.2.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5000",
        "https://scalpyn.vercel.app",
        "https://www.scalpyn.vercel.app",
    ],
    allow_origin_regex=r"https://.*\.(replit\.app|replit\.dev|repl\.co|vercel\.app)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schema-readiness gate (ASYNC_MIGRATIONS=1 only) ─────────────────────────
# When start.sh runs the schema gate (alembic + validate_critical_schema) in a
# background subshell, uvicorn binds :8080 immediately to clear Cloud Run's
# startup probe. While that gate is in flight there is a real window where
# request handlers could touch columns that have not been created yet.
#
# This middleware closes the window: until /tmp/.migrations_done exists, every
# non-allowlisted route returns 503 with a Retry-After hint. The allowlist is
# intentionally narrow — only health and metrics endpoints, which are safe to
# serve before the schema is fully converged. Once the sentinel is created the
# middleware short-circuits and adds zero overhead per request.
#
# Failure path: if the gate fails, start.sh's watchdog SIGTERMs the container
# (Cloud Run rolls back to the previous revision). This middleware does not
# need to handle that — it only protects the *in-flight* window.
import os as _os
from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware
from starlette.responses import JSONResponse as _JSONResponse

_SCHEMA_READY_PATHS = (
    "/api/health",          # covers /api/health and /api/health/schema
    "/metrics",
    "/api/system/persistence",
)
_SCHEMA_DONE_FILE = "/tmp/.migrations_done"
_ASYNC_MIGRATIONS = _os.environ.get("ASYNC_MIGRATIONS") == "1"
_schema_ready_cached = False  # one-way latch; once True we never check again


class _SchemaReadinessGate(_BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        global _schema_ready_cached
        if _schema_ready_cached or not _ASYNC_MIGRATIONS:
            return await call_next(request)
        if _os.path.exists(_SCHEMA_DONE_FILE):
            _schema_ready_cached = True
            return await call_next(request)
        path = request.url.path
        if any(path.startswith(p) for p in _SCHEMA_READY_PATHS):
            return await call_next(request)
        return _JSONResponse(
            status_code=503,
            headers={"Retry-After": "10"},
            content={
                "status": "warming_up",
                "detail": (
                    "Schema migrations are still running. "
                    "Probe /api/health/schema for full readiness state."
                ),
            },
        )


app.add_middleware(_SchemaReadinessGate)

# Auth & Config
app.include_router(auth.router)
app.include_router(config_api.router)
app.include_router(decisions.router)

# Market Data & Watchlist
app.include_router(market.router)
app.include_router(watchlist.router)
app.include_router(watchlists.router)
app.include_router(watchlists.pipeline_router)
app.include_router(custom_watchlists.router)

# Trading
app.include_router(trades.router)
app.include_router(orders.router)
app.include_router(pools.router)
app.include_router(exchanges.router)

# Trading Engines
app.include_router(spot_engine.router)
app.include_router(futures_engine.router)

# Asset Search
app.include_router(asset_search.router)

# Strategy Profiles
app.include_router(profiles.router)

# Analytics & Reports
app.include_router(analytics.router)
app.include_router(reports.router)
app.include_router(backoffice.router)

# Simulations
app.include_router(simulations.router)

# System health
app.include_router(system.router)

# Admin diagnostics (bearer-token gated, hidden if env var unset)
from .api import admin_diagnostics  # noqa: E402
app.include_router(admin_diagnostics.router)

# Encryption health + admin rewrap (Task #275)
from .api import encryption_admin  # noqa: E402
app.include_router(encryption_admin.router)

# Trade decision diagnostics — read-only views over trade_decisions
# (joined by trace_id) for debugging the L3 → execution gate → exchange
# decision flow.
from .api import trade_diagnostics  # noqa: E402
app.include_router(trade_diagnostics.router)

# Live operational endpoints — SSE decision log stream + balance/positions
# polls. Tenancy is enforced server-side via ``get_current_user_id``.
from .api import live_log_stream  # noqa: E402
app.include_router(live_log_stream.router)

# Machine Learning
app.include_router(ml.router)

# Notifications
app.include_router(notifications.router)

# AI Provider Keys
app.include_router(ai_keys.router)

# AI Skills
app.include_router(ai_skills.router)

# Debug / Diagnostic endpoints
app.include_router(debug_indicators.router)

# Debug — manual OHLCV collector trigger (Task #221).
# Hidden by default in Cloud Run when DEBUG_COLLECT_TOKEN is unset.
from .api import debug_collect  # noqa: E402
app.include_router(debug_collect.router)

# Prometheus /metrics (robust-indicators Phase 1)
app.include_router(metrics_api.router)

# Operational performance dashboard (Task #224) — read-only aggregations
# powering the /dashboard/performance Next.js page.
from .api import dashboard as dashboard_api  # noqa: E402
app.include_router(dashboard_api.router)

# Institutional performance dashboard (Task #257) — read-only aggregations
# over `position_lifecycle` powering /dashboard/performance.
from .api import performance as performance_api  # noqa: E402
app.include_router(performance_api.router)

# Shadow Portfolio (Task #289) — read-only endpoints sobre `shadow_trades`
# para a aba Shadow Trade do frontend (lista paginada / summary / detail).
from .api import shadow_trades as shadow_trades_api  # noqa: E402
app.include_router(shadow_trades_api.router)

# WebSocket
app.include_router(websocket.router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/api/health/schema")
async def health_check_schema():
    """Probes information_schema for columns the ORM relies on.

    Returns 503 with the list of missing (table, column) pairs if any are
    absent.  Detects schema drift even when init_db() reported success or
    never ran — that's the silent-failure mode that caused two production
    incidents (market_mode in Task #41, last_scanned_at right after).
    Wire Cloud Run / external monitors at this endpoint, not /api/health.
    """
    import json as _json
    from fastapi import Response
    from sqlalchemy import text
    from .database import AsyncSessionLocal
    from ._critical_schema import CRITICAL_COLUMNS as critical_columns

    try:
        async with AsyncSessionLocal() as sess:
            rows = (await sess.execute(text("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
            """))).all()
        present = {(r[0], r[1]) for r in rows}
        missing = [
            {"table": t, "column": c}
            for (t, c) in critical_columns
            if (t, c) not in present
        ]
    except Exception as e:
        return Response(
            content=_json.dumps({
                "schema_ok": False,
                "error": f"{type(e).__name__}: {e}",
                "missing": [],
                "checked": len(critical_columns),
            }),
            status_code=503,
            media_type="application/json",
        )

    # Task #234 hotfix — surface the alpha_scores.scoring_version drift
    # in /api/health/schema for operator visibility WITHOUT promoting it
    # to CRITICAL_COLUMNS yet (N+1 deploy rule). The runtime fallback in
    # compute_scores already keeps scoring alive; this field lets the
    # operator see whether the column has landed before opening Task #235
    # to flip CRITICAL_COLUMNS in deploy N+1.
    scoring_version_present = (
        ("alpha_scores", "scoring_version") in present
    )
    if not scoring_version_present:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[HEALTH/SCHEMA] alpha_scores.scoring_version absent — "
            "compute_scores running on legacy fallback INSERT (Task #234). "
            "After repair, ship Task #235 to add the column to CRITICAL_COLUMNS."
        )

    payload = {
        "schema_ok": len(missing) == 0,
        "checked_count": len(critical_columns),
        "checked": [{"table": t, "column": c} for (t, c) in critical_columns],
        "missing": missing,
        "advisory": {
            # Non-blocking probes (do not flip schema_ok / status code).
            "alpha_scores.scoring_version": scoring_version_present,
        },
    }
    if missing:
        return Response(
            content=_json.dumps(payload),
            status_code=503,
            media_type="application/json",
        )
    return payload


@app.get("/api/market/scores")
async def get_market_scores():
    """Get current Alpha Score ranking for all tracked symbols."""
    from sqlalchemy import text
    from .database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(text("""
                SELECT DISTINCT ON (symbol)
                    symbol, score, liquidity_score, market_structure_score,
                    momentum_score, signal_score, time
                FROM alpha_scores
                WHERE time > now() - interval '3 hours'
                ORDER BY symbol, time DESC
            """))
            rows = result.fetchall()

            scores = sorted([
                {
                    "symbol": r.symbol,
                    "score": float(r.score),
                    "liquidity": float(r.liquidity_score) if r.liquidity_score else 0,
                    "market_structure": float(r.market_structure_score) if r.market_structure_score else 0,
                    "momentum": float(r.momentum_score) if r.momentum_score else 0,
                    "signal": float(r.signal_score) if r.signal_score else 0,
                    "updated": r.time.isoformat(),
                }
                for r in rows
            ], key=lambda x: x["score"], reverse=True)

            return {"scores": scores, "total": len(scores)}
    except Exception:
        return {"scores": [], "total": 0}
