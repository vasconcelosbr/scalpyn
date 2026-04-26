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
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    _log = logging.getLogger(__name__)

    # Schema bootstrap is now owned by start.sh in production (alembic upgrade
    # head + python -m app.init_db, both gating container startup).  In dev,
    # the workflow command runs uvicorn directly without start.sh, so we still
    # bootstrap here as a convenience.  The /api/health/schema endpoint below
    # queries information_schema directly and does not depend on this running.
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

    yield


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

# Asset Search
app.include_router(asset_search.router)

# Strategy Profiles
app.include_router(profiles.router)

# Analytics & Reports
app.include_router(analytics.router)
app.include_router(reports.router)
app.include_router(backoffice.router)

# Notifications
app.include_router(notifications.router)

# AI Provider Keys
app.include_router(ai_keys.router)

# AI Skills
app.include_router(ai_skills.router)

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

    # Critical (table, column) pairs — every column declared by an ORM model
    # whose absence would 500 a user-facing endpoint.  Keep in sync with
    # backend/alembic/versions/021_init_db_parity_catchall.py.
    critical_columns = [
        ("pools", "overrides"),
        ("pools", "autopilot_enabled"),
        ("pipeline_watchlists", "market_mode"),
        ("pipeline_watchlists", "last_scanned_at"),
        ("pipeline_watchlist_assets", "execution_id"),
        ("pipeline_watchlist_assets", "score_long"),
        ("pipeline_watchlist_assets", "score_short"),
        ("pipeline_watchlist_assets", "confidence_score"),
        ("pipeline_watchlist_assets", "futures_direction"),
        ("pipeline_watchlist_assets", "entry_long_blocked"),
        ("pipeline_watchlist_assets", "entry_short_blocked"),
        ("pipeline_watchlist_assets", "refreshed_at"),
        ("pipeline_watchlist_assets", "analysis_snapshot"),
        ("pipeline_watchlist_rejections", "execution_id"),
        ("pipeline_watchlist_rejections", "analysis_snapshot"),
        ("watchlist_profiles", "profile_type"),
        ("trades", "exchange_order_id"),
        ("trades", "source"),
    ]

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

    payload = {
        "schema_ok": len(missing) == 0,
        "checked": len(critical_columns),
        "missing": missing,
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
