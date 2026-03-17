"""Custom Watchlists API — CRUD operations for user watchlists."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Any, List
from uuid import UUID
import logging

from ..database import get_db
from ..models.custom_watchlist import CustomWatchlist
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/custom-watchlists", tags=["Custom Watchlists"])


def _watchlist_to_dict(wl: CustomWatchlist) -> Dict[str, Any]:
    """Convert CustomWatchlist model to dict."""
    return {
        "id": str(wl.id),
        "name": wl.name,
        "description": wl.description,
        "symbols": wl.symbols or [],
        "symbol_count": len(wl.symbols) if wl.symbols else 0,
        "is_active": wl.is_active,
        "created_at": wl.created_at.isoformat() if wl.created_at else None,
        "updated_at": wl.updated_at.isoformat() if wl.updated_at else None,
    }


@router.get("/")
async def get_custom_watchlists(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Get all custom watchlists for current user."""
    query = select(CustomWatchlist).where(
        CustomWatchlist.user_id == user_id
    ).order_by(CustomWatchlist.created_at.desc())
    
    result = await db.execute(query)
    watchlists = result.scalars().all()
    
    return {"watchlists": [_watchlist_to_dict(wl) for wl in watchlists]}


@router.get("/{watchlist_id}")
async def get_custom_watchlist(
    watchlist_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Get a single custom watchlist by ID."""
    query = select(CustomWatchlist).where(
        CustomWatchlist.id == watchlist_id,
        CustomWatchlist.user_id == user_id
    )
    result = await db.execute(query)
    wl = result.scalars().first()
    
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    
    return _watchlist_to_dict(wl)


@router.post("/")
async def create_custom_watchlist(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """
    Create a new custom watchlist.
    
    Payload:
    {
        "name": "My Watchlist",
        "description": "Optional description",
        "symbols": ["BTCUSDT", "ETHUSDT", ...]
    }
    """
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Watchlist name is required")
    
    symbols = payload.get("symbols", [])
    if not isinstance(symbols, list):
        symbols = []
    
    # Normalize symbols to uppercase
    symbols = [s.upper().strip() for s in symbols if isinstance(s, str)]
    
    wl = CustomWatchlist(
        user_id=user_id,
        name=name,
        description=payload.get("description", ""),
        symbols=symbols,
        is_active=True
    )
    
    db.add(wl)
    await db.commit()
    await db.refresh(wl)
    
    return _watchlist_to_dict(wl)


@router.put("/{watchlist_id}")
async def update_custom_watchlist(
    watchlist_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Update a custom watchlist."""
    query = select(CustomWatchlist).where(
        CustomWatchlist.id == watchlist_id,
        CustomWatchlist.user_id == user_id
    )
    result = await db.execute(query)
    wl = result.scalars().first()
    
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    
    if "name" in payload:
        wl.name = payload["name"]
    if "description" in payload:
        wl.description = payload["description"]
    if "symbols" in payload:
        symbols = payload["symbols"]
        if isinstance(symbols, list):
            wl.symbols = [s.upper().strip() for s in symbols if isinstance(s, str)]
    if "is_active" in payload:
        wl.is_active = payload["is_active"]
    
    await db.commit()
    await db.refresh(wl)
    
    return _watchlist_to_dict(wl)


@router.delete("/{watchlist_id}")
async def delete_custom_watchlist(
    watchlist_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Delete a custom watchlist."""
    query = select(CustomWatchlist).where(
        CustomWatchlist.id == watchlist_id,
        CustomWatchlist.user_id == user_id
    )
    result = await db.execute(query)
    wl = result.scalars().first()
    
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    
    await db.delete(wl)
    await db.commit()
    
    return {"status": "success", "message": "Watchlist deleted"}


@router.post("/{watchlist_id}/symbols")
async def add_symbols_to_watchlist(
    watchlist_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """
    Add symbols to a watchlist.
    
    Payload: {"symbols": ["BTCUSDT", "ETHUSDT"]}
    """
    query = select(CustomWatchlist).where(
        CustomWatchlist.id == watchlist_id,
        CustomWatchlist.user_id == user_id
    )
    result = await db.execute(query)
    wl = result.scalars().first()
    
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    
    new_symbols = payload.get("symbols", [])
    if not isinstance(new_symbols, list):
        new_symbols = []
    
    new_symbols = [s.upper().strip() for s in new_symbols if isinstance(s, str)]
    
    # Add new symbols, avoiding duplicates
    existing = set(wl.symbols or [])
    updated = list(existing.union(set(new_symbols)))
    wl.symbols = updated
    
    await db.commit()
    await db.refresh(wl)
    
    return _watchlist_to_dict(wl)


@router.delete("/{watchlist_id}/symbols/{symbol}")
async def remove_symbol_from_watchlist(
    watchlist_id: UUID,
    symbol: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Remove a symbol from a watchlist."""
    query = select(CustomWatchlist).where(
        CustomWatchlist.id == watchlist_id,
        CustomWatchlist.user_id == user_id
    )
    result = await db.execute(query)
    wl = result.scalars().first()
    
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    
    symbol = symbol.upper().strip()
    if wl.symbols and symbol in wl.symbols:
        wl.symbols = [s for s in wl.symbols if s != symbol]
        await db.commit()
        await db.refresh(wl)
    
    return _watchlist_to_dict(wl)


@router.post("/sync")
async def sync_watchlists(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """
    Sync watchlists from localStorage to database.
    
    Payload:
    {
        "watchlists": [
            {"id": "local-id", "name": "My List", "symbols": ["BTCUSDT"]}
        ]
    }
    """
    local_watchlists = payload.get("watchlists", [])
    synced = []
    
    for local_wl in local_watchlists:
        local_id = local_wl.get("id", "")
        name = local_wl.get("name", "Untitled")
        symbols = local_wl.get("symbols", [])
        
        # Extract symbol strings from items if needed
        if symbols and isinstance(symbols[0], dict):
            symbols = [s.get("symbol", "") for s in symbols]
        
        symbols = [s.upper().strip() for s in symbols if isinstance(s, str) and s]
        
        # Create new watchlist in database
        wl = CustomWatchlist(
            user_id=user_id,
            name=name,
            symbols=symbols,
            is_active=True
        )
        db.add(wl)
        await db.flush()
        
        synced.append({
            "local_id": local_id,
            "server_id": str(wl.id),
            "name": name,
            "symbol_count": len(symbols)
        })
    
    await db.commit()
    
    return {
        "status": "success",
        "synced_count": len(synced),
        "watchlists": synced
    }
