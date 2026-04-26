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

    # Track init_db outcome so /api/health/schema can report drift.  Without
    # this, a silent init_db failure (timeout, missing column) lets uvicorn
    # serve traffic with a broken schema — that's the bug that hid Pipeline
    # watchlists from the user (Task #41).
    app.state.init_db_error = None
    app.state.init_db_ok = False

    try:
        _log.info("Initializing database schema...")
        await asyncio.wait_for(init_db(), timeout=30)
        app.state.init_db_ok = True
    except asyncio.TimeoutError:
        msg = "Database initialization timed out after 30 s — continuing without schema sync"
        _log.warning(msg)
        app.state.init_db_error = msg
    except Exception as e:
        msg = f"Database initialization error: {type(e).__name__}: {e}"
        _log.error(msg, exc_info=True)
        app.state.init_db_error = msg

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
    """Reports whether init_db() finished successfully on startup.

    Returns 503 if the schema bootstrap failed or timed out — that's the
    silent-failure mode that hid Pipeline watchlists in production (Task #41).
    Lets ops/CI catch schema drift instead of relying on user reports.
    """
    from fastapi import Response
    payload = {
        "init_db_ok": getattr(app.state, "init_db_ok", False),
        "init_db_error": getattr(app.state, "init_db_error", None),
    }
    if not payload["init_db_ok"]:
        return Response(
            content=__import__("json").dumps(payload),
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
