"""Pipeline Watchlist API — CRUD + live results from pipeline_scan task.

Endpoints:
  GET  /api/pipeline                       → list all pipeline watchlists
  POST /api/pipeline                       → create pipeline watchlist
  GET  /api/pipeline/{wl_id}               → get watchlist details
  PUT  /api/pipeline/{wl_id}               → update watchlist
  DELETE /api/pipeline/{wl_id}             → delete watchlist
  GET  /api/pipeline/{wl_id}/assets        → live assets (from pipeline_watchlist_assets)
  POST /api/pipeline/{wl_id}/refresh       → trigger immediate scan for this watchlist
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from ..models.profile import Profile
from ..models.pool import Pool
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["Pipeline"])


# ─── helpers ──────────────────────────────────────────────────────────────────

def _wl_to_dict(wl: PipelineWatchlist) -> Dict[str, Any]:
    return {
        "id":                   str(wl.id),
        "name":                 wl.name,
        "level":                wl.level,
        "source_pool_id":       str(wl.source_pool_id)       if wl.source_pool_id       else None,
        "source_watchlist_id":  str(wl.source_watchlist_id)  if wl.source_watchlist_id  else None,
        "profile_id":           str(wl.profile_id)           if wl.profile_id           else None,
        "auto_refresh":         wl.auto_refresh,
        "filters_json":         wl.filters_json or {},
        "created_at":           wl.created_at.isoformat()    if wl.created_at           else None,
        "updated_at":           wl.updated_at.isoformat()    if wl.updated_at           else None,
    }


async def _get_own_wl(db: AsyncSession, wl_id: UUID, user_id: UUID) -> PipelineWatchlist:
    result = await db.execute(
        select(PipelineWatchlist).where(
            PipelineWatchlist.id == wl_id,
            PipelineWatchlist.user_id == user_id,
        )
    )
    wl = result.scalars().first()
    if not wl:
        raise HTTPException(status_code=404, detail="Pipeline watchlist not found")
    return wl


# ─── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_pipeline_watchlists(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """List all pipeline watchlists for the current user."""
    rows = (await db.execute(
        select(PipelineWatchlist)
        .where(PipelineWatchlist.user_id == user_id)
        .order_by(PipelineWatchlist.level, PipelineWatchlist.created_at)
    )).scalars().all()
    return {"watchlists": [_wl_to_dict(wl) for wl in rows]}


@router.post("/")
async def create_pipeline_watchlist(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Create a pipeline watchlist.

    Body fields:
      name                (str, required)
      level               "L1" | "L2" | "L3"
      source_pool_id      UUID of a Pool   (L1 sources from Pool)
      source_watchlist_id UUID of another PipelineWatchlist (L2/L3)
      profile_id          UUID of a Profile to apply
      auto_refresh        bool (default true)
      filters_json        {"min_score": 60, "require_signal": true}
    """
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    level = (payload.get("level") or "L1").upper()
    if level not in ("L1", "L2", "L3"):
        raise HTTPException(status_code=400, detail="level must be L1, L2 or L3")

    wl = PipelineWatchlist(
        user_id=user_id,
        name=name,
        level=level,
        source_pool_id=payload.get("source_pool_id"),
        source_watchlist_id=payload.get("source_watchlist_id"),
        profile_id=payload.get("profile_id"),
        auto_refresh=payload.get("auto_refresh", True),
        filters_json=payload.get("filters_json", {}),
    )
    db.add(wl)
    await db.commit()
    await db.refresh(wl)
    return _wl_to_dict(wl)


@router.get("/{wl_id}")
async def get_pipeline_watchlist(
    wl_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    wl = await _get_own_wl(db, wl_id, user_id)
    return _wl_to_dict(wl)


@router.put("/{wl_id}")
async def update_pipeline_watchlist(
    wl_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    wl = await _get_own_wl(db, wl_id, user_id)

    for field in ("name", "level", "source_pool_id", "source_watchlist_id",
                  "profile_id", "auto_refresh", "filters_json"):
        if field in payload:
            setattr(wl, field, payload[field])

    await db.commit()
    await db.refresh(wl)
    return _wl_to_dict(wl)


@router.delete("/{wl_id}")
async def delete_pipeline_watchlist(
    wl_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    wl = await _get_own_wl(db, wl_id, user_id)
    await db.delete(wl)
    await db.commit()
    return {"status": "deleted", "id": str(wl_id)}


# ─── Live assets ──────────────────────────────────────────────────────────────

@router.get("/{wl_id}/assets")
async def get_pipeline_assets(
    wl_id: UUID,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Return the current asset snapshot stored by the pipeline_scan task.
    No re-computation — just reads what the Celery task last persisted.
    """
    wl = await _get_own_wl(db, wl_id, user_id)

    rows = (await db.execute(text("""
        SELECT symbol, current_price, price_change_24h,
               volume_24h, market_cap, alpha_score, entered_at
        FROM   pipeline_watchlist_assets
        WHERE  watchlist_id = :wid
        ORDER  BY alpha_score DESC NULLS LAST
        LIMIT  :limit
    """), {"wid": str(wl_id), "limit": limit})).fetchall()

    assets = [
        {
            "symbol":       r.symbol,
            "price":        float(r.current_price)    if r.current_price    else 0.0,
            "change_24h":   float(r.price_change_24h) if r.price_change_24h else 0.0,
            "volume_24h":   float(r.volume_24h)       if r.volume_24h       else 0.0,
            "market_cap":   float(r.market_cap)       if r.market_cap       else 0.0,
            "score":        float(r.alpha_score)       if r.alpha_score       else 0.0,
            "entered_at":   r.entered_at.isoformat()  if r.entered_at       else None,
        }
        for r in rows
    ]

    return {
        "watchlist_id":   str(wl_id),
        "watchlist_name": wl.name,
        "level":          wl.level,
        "asset_count":    len(assets),
        "assets":         assets,
    }


# ─── Manual refresh ───────────────────────────────────────────────────────────

@router.post("/{wl_id}/refresh")
async def refresh_pipeline_watchlist(
    wl_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Trigger an immediate pipeline evaluation for this watchlist
    (runs inline, not via Celery — useful for testing and manual refresh).
    """
    from ..tasks.pipeline_scan import _run_pipeline_scan

    wl = await _get_own_wl(db, wl_id, user_id)

    import asyncio
    try:
        # Run only for this specific watchlist by executing the full scan
        # (the scan itself is efficient — it queries all watchlists at once)
        stats = await _run_pipeline_scan()
        assets_result = await get_pipeline_assets(wl_id, 100, db, user_id)
        return {
            "status":   "refreshed",
            "stats":    stats,
            "assets":   assets_result["assets"],
            "count":    assets_result["asset_count"],
        }
    except Exception as exc:
        logger.exception("Manual pipeline refresh failed for %s: %s", wl_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
