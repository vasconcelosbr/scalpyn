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



# ============================================================================
# EXECUTION PIPELINE — L2 RANKING & L3 SIGNALS
# ============================================================================

@router.get("/{watchlist_id}/ranking")
async def get_watchlist_ranking(
    watchlist_id: UUID,
    top_n: int = 20,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """
    L2 RANKING — Generate dynamic ranking using assigned profile.
    
    Pipeline:
    1. Load watchlist assets
    2. Load assigned profile
    3. Apply L1 filters
    4. Compute Alpha Score with L2 weights
    5. Return top N ranked assets
    """
    from sqlalchemy import text
    from ..models.profile import Profile, WatchlistProfile
    from ..services.profile_engine import ProfileEngine
    
    # Get watchlist
    wl_query = select(CustomWatchlist).where(
        CustomWatchlist.id == watchlist_id,
        CustomWatchlist.user_id == user_id
    )
    wl_result = await db.execute(wl_query)
    watchlist = wl_result.scalars().first()
    
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    
    # Get assigned profile
    profile_assignment = await db.execute(
        select(WatchlistProfile).where(
            WatchlistProfile.user_id == user_id,
            WatchlistProfile.watchlist_id == str(watchlist_id)
        )
    )
    assignment = profile_assignment.scalars().first()
    
    profile_config = None
    profile_name = "Default"
    
    if assignment and assignment.profile_id and assignment.is_enabled:
        profile_result = await db.execute(
            select(Profile).where(Profile.id == assignment.profile_id)
        )
        profile = profile_result.scalars().first()
        if profile:
            profile_config = profile.config
            profile_name = profile.name
    
    # Get market data for watchlist symbols
    assets = await _get_assets_with_indicators(db, watchlist.symbols)
    
    if not assets:
        return {
            "watchlist": watchlist.name,
            "watchlist_id": str(watchlist_id),
            "profile": profile_name,
            "total_assets": 0,
            "filtered_assets": 0,
            "assets": []
        }
    
    # Process through profile engine
    engine = ProfileEngine(profile_config)
    
    # Apply L1 filters
    filtered = engine._apply_filters(assets)
    
    # Compute L2 scores
    scored_assets = []
    for asset in filtered:
        processed = engine._process_single_asset(asset, include_details=False)
        scored_assets.append({
            "symbol": processed.get("symbol"),
            "name": processed.get("name"),
            "price": processed.get("price"),
            "change_24h": processed.get("change_24h"),
            "volume_24h": asset.get("volume_24h"),
            "score": processed.get("score", {}).get("total_score", 0),
            "score_breakdown": {
                "liquidity": processed.get("score", {}).get("liquidity", 0),
                "market_structure": processed.get("score", {}).get("market_structure", 0),
                "momentum": processed.get("score", {}).get("momentum", 0),
                "signal": processed.get("score", {}).get("signal", 0),
            },
            "rating": _get_rating(processed.get("score", {}).get("total_score", 0))
        })
    
    # Sort by score descending and limit
    scored_assets.sort(key=lambda x: x["score"], reverse=True)
    top_assets = scored_assets[:top_n]
    
    return {
        "watchlist": watchlist.name,
        "watchlist_id": str(watchlist_id),
        "profile": profile_name,
        "total_assets": len(assets),
        "filtered_assets": len(filtered),
        "top_n": top_n,
        "assets": top_assets
    }


@router.get("/{watchlist_id}/signals")
async def get_watchlist_signals(
    watchlist_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """
    L3 SIGNALS — Generate actionable signals using assigned profile.
    
    Pipeline:
    1. Load watchlist assets
    2. Load assigned profile
    3. Apply L1 filters
    4. Compute L2 scores
    5. Evaluate L3 signal conditions
    6. Return only assets with triggered signals
    """
    from sqlalchemy import text
    from ..models.profile import Profile, WatchlistProfile
    from ..services.profile_engine import ProfileEngine
    
    # Get watchlist
    wl_query = select(CustomWatchlist).where(
        CustomWatchlist.id == watchlist_id,
        CustomWatchlist.user_id == user_id
    )
    wl_result = await db.execute(wl_query)
    watchlist = wl_result.scalars().first()
    
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    
    # Get assigned profile
    profile_assignment = await db.execute(
        select(WatchlistProfile).where(
            WatchlistProfile.user_id == user_id,
            WatchlistProfile.watchlist_id == str(watchlist_id)
        )
    )
    assignment = profile_assignment.scalars().first()
    
    profile_config = None
    profile_name = "Default"
    signal_conditions_count = 0
    
    if assignment and assignment.profile_id and assignment.is_enabled:
        profile_result = await db.execute(
            select(Profile).where(Profile.id == assignment.profile_id)
        )
        profile = profile_result.scalars().first()
        if profile:
            profile_config = profile.config
            profile_name = profile.name
            signal_conditions_count = len(profile_config.get("signals", {}).get("conditions", []))
    
    # Get market data for watchlist symbols
    assets = await _get_assets_with_indicators(db, watchlist.symbols)
    
    if not assets:
        return {
            "watchlist": watchlist.name,
            "watchlist_id": str(watchlist_id),
            "profile": profile_name,
            "total_assets": 0,
            "signals_count": 0,
            "signals": []
        }
    
    # Process through profile engine
    engine = ProfileEngine(profile_config)
    result = engine.process_watchlist(assets, include_details=True)
    
    # Extract only assets with triggered signals
    signals = []
    for asset in result["assets"]:
        if asset.get("signal", {}).get("triggered"):
            signals.append({
                "symbol": asset.get("symbol"),
                "name": asset.get("name"),
                "price": asset.get("price"),
                "change_24h": asset.get("change_24h"),
                "action": _determine_action(asset),
                "score": asset.get("score", {}).get("total_score", 0),
                "confidence": _calculate_confidence(asset),
                "matched_conditions": asset.get("signal", {}).get("matched_conditions", []),
                "rating": _get_rating(asset.get("score", {}).get("total_score", 0))
            })
    
    # Sort by score descending
    signals.sort(key=lambda x: x["score"], reverse=True)
    
    return {
        "watchlist": watchlist.name,
        "watchlist_id": str(watchlist_id),
        "profile": profile_name,
        "signal_conditions": signal_conditions_count,
        "total_assets": result["total_before_filter"],
        "filtered_assets": result["total_after_filter"],
        "signals_count": len(signals),
        "signals": signals
    }


@router.put("/{watchlist_id}/profile")
async def assign_profile_to_watchlist(
    watchlist_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Assign a profile to a watchlist for L2/L3 processing."""
    from ..models.profile import Profile, WatchlistProfile
    
    # Verify watchlist exists
    wl_query = select(CustomWatchlist).where(
        CustomWatchlist.id == watchlist_id,
        CustomWatchlist.user_id == user_id
    )
    wl_result = await db.execute(wl_query)
    watchlist = wl_result.scalars().first()
    
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    
    profile_id = payload.get("profile_id")
    
    # Verify profile if provided
    if profile_id:
        profile_query = select(Profile).where(
            Profile.id == profile_id,
            Profile.user_id == user_id
        )
        profile_result = await db.execute(profile_query)
        if not profile_result.scalars().first():
            raise HTTPException(status_code=404, detail="Profile not found")
    
    # Check existing assignment
    assignment_query = select(WatchlistProfile).where(
        WatchlistProfile.user_id == user_id,
        WatchlistProfile.watchlist_id == str(watchlist_id)
    )
    assignment_result = await db.execute(assignment_query)
    existing = assignment_result.scalars().first()
    
    if existing:
        existing.profile_id = profile_id
        existing.is_enabled = True
    else:
        new_assignment = WatchlistProfile(
            user_id=user_id,
            watchlist_id=str(watchlist_id),
            profile_id=profile_id,
            is_enabled=True
        )
        db.add(new_assignment)
    
    await db.commit()
    
    return {
        "status": "success",
        "watchlist_id": str(watchlist_id),
        "profile_id": str(profile_id) if profile_id else None
    }


@router.get("/{watchlist_id}/profile")
async def get_watchlist_assigned_profile(
    watchlist_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Get the profile assigned to a watchlist."""
    from ..models.profile import Profile, WatchlistProfile
    
    assignment_query = select(WatchlistProfile).where(
        WatchlistProfile.user_id == user_id,
        WatchlistProfile.watchlist_id == str(watchlist_id)
    )
    assignment_result = await db.execute(assignment_query)
    assignment = assignment_result.scalars().first()
    
    if not assignment or not assignment.profile_id:
        return {
            "watchlist_id": str(watchlist_id),
            "profile": None,
            "is_enabled": False
        }
    
    profile_query = select(Profile).where(Profile.id == assignment.profile_id)
    profile_result = await db.execute(profile_query)
    profile = profile_result.scalars().first()
    
    if not profile:
        return {
            "watchlist_id": str(watchlist_id),
            "profile": None,
            "is_enabled": False
        }
    
    return {
        "watchlist_id": str(watchlist_id),
        "profile": {
            "id": str(profile.id),
            "name": profile.name,
            "description": profile.description,
            "config": profile.config
        },
        "is_enabled": assignment.is_enabled
    }


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def _get_assets_with_indicators(db: AsyncSession, symbols: List[str]) -> List[Dict[str, Any]]:
    """Get market data and indicators for specified symbols."""
    from sqlalchemy import text
    
    if not symbols:
        return []
    
    try:
        # Get market metadata
        symbols_str = ",".join([f"'{s}'" for s in symbols])
        
        # Try to get from market_metadata first
        metadata_query = text(f"""
            SELECT symbol, name, market_cap, volume_24h, price, price_change_24h
            FROM market_metadata
            WHERE symbol IN ({symbols_str})
        """)
        metadata_result = await db.execute(metadata_query)
        metadata_rows = metadata_result.fetchall()
        
        # Get latest indicators
        indicators_query = text(f"""
            SELECT DISTINCT ON (symbol)
                symbol, indicators_json
            FROM indicators
            WHERE symbol IN ({symbols_str})
            ORDER BY symbol, time DESC
        """)
        try:
            indicators_result = await db.execute(indicators_query)
            indicators_rows = indicators_result.fetchall()
            indicators_map = {r.symbol: r.indicators_json or {} for r in indicators_rows}
        except Exception:
            indicators_map = {}
        
        # Get latest scores
        scores_query = text(f"""
            SELECT DISTINCT ON (symbol)
                symbol, score, liquidity_score, market_structure_score,
                momentum_score, signal_score
            FROM alpha_scores
            WHERE symbol IN ({symbols_str})
            ORDER BY symbol, time DESC
        """)
        try:
            scores_result = await db.execute(scores_query)
            scores_rows = scores_result.fetchall()
            scores_map = {r.symbol: r for r in scores_rows}
        except Exception:
            scores_map = {}
        
        assets = []
        for row in metadata_rows:
            indicators = indicators_map.get(row.symbol, {})
            score_row = scores_map.get(row.symbol)
            
            asset = {
                "symbol": row.symbol,
                "name": row.name or row.symbol,
                "price": float(row.price) if row.price else 0,
                "change_24h": float(row.price_change_24h) if row.price_change_24h else 0,
                "market_cap": float(row.market_cap) if row.market_cap else 0,
                "volume_24h": float(row.volume_24h) if row.volume_24h else 0,
                "indicators": indicators,
            }
            
            # Flatten indicators for filtering
            for k, v in indicators.items():
                if isinstance(v, (int, float, bool, str)):
                    asset[k] = v
            
            # Add existing scores if available
            if score_row:
                asset["existing_score"] = float(score_row.score) if score_row.score else 0
                asset["liquidity_score"] = float(score_row.liquidity_score) if score_row.liquidity_score else 0
                asset["market_structure_score"] = float(score_row.market_structure_score) if score_row.market_structure_score else 0
                asset["momentum_score"] = float(score_row.momentum_score) if score_row.momentum_score else 0
                asset["signal_score"] = float(score_row.signal_score) if score_row.signal_score else 0
            
            assets.append(asset)
        
        # If no metadata found, create placeholder assets
        if not assets:
            for symbol in symbols:
                assets.append({
                    "symbol": symbol,
                    "name": symbol,
                    "price": 0,
                    "change_24h": 0,
                    "market_cap": 0,
                    "volume_24h": 0,
                    "indicators": {},
                })
        
        return assets
    except Exception as e:
        logger.warning(f"Failed to get assets with indicators: {e}")
        # Return minimal assets
        return [{"symbol": s, "name": s, "price": 0, "change_24h": 0, "indicators": {}} for s in symbols]


def _get_rating(score: float) -> str:
    """Convert score to rating label."""
    if score >= 80:
        return "STRONG_BUY"
    elif score >= 65:
        return "BUY"
    elif score >= 40:
        return "NEUTRAL"
    else:
        return "AVOID"


def _determine_action(asset: Dict[str, Any]) -> str:
    """Determine trading action based on asset data."""
    score = asset.get("score", {}).get("total_score", 0)
    change = asset.get("change_24h", 0)
    
    # Simple logic - can be enhanced
    if score >= 60:
        return "LONG"
    elif score <= 30:
        return "SHORT"
    return "HOLD"


def _calculate_confidence(asset: Dict[str, Any]) -> float:
    """Calculate confidence score for a signal."""
    signal = asset.get("signal", {})
    matched = len(signal.get("matched_conditions", []))
    failed_required = len(signal.get("failed_required", []))
    
    # Base confidence on how many conditions matched
    if matched == 0:
        return 0.5
    
    # Higher confidence if more conditions matched and no required failed
    confidence = min(0.95, 0.5 + (matched * 0.1))
    
    if failed_required > 0:
        confidence *= 0.7  # Reduce if required conditions failed
    
    return round(confidence, 2)
