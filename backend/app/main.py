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

    # Time-box DB init so a slow/unreachable DB never blocks the startup
    # probe.  The health endpoint still works without the DB.
    try:
        _log.info("Initializing database schema...")
        await asyncio.wait_for(init_db(), timeout=30)
    except asyncio.TimeoutError:
        _log.warning("Database initialization timed out after 30 s — continuing without schema sync")
    except Exception as e:
        _log.error("Database initialization error: %s", e)

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
