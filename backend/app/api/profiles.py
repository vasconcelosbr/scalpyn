"""Profiles API — CRUD operations for strategy profiles."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, delete
from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime, timezone
import logging

from ..database import get_db
from ..models.profile import Profile, WatchlistProfile
from .config import get_current_user_id
from ..services.profile_engine import ProfileEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profiles", tags=["Profiles"])


def _profile_to_dict(profile: Profile) -> Dict[str, Any]:
    """Convert Profile model to dict."""
    return {
        "id": str(profile.id),
        "name": profile.name,
        "description": profile.description,
        "is_active": profile.is_active,
        "config": profile.config or {},
        "profile_role":        getattr(profile, "profile_role", None),
        "pipeline_order":      getattr(profile, "pipeline_order", "99"),
        "pipeline_label":      getattr(profile, "pipeline_label", None),
        "auto_pilot_enabled":  getattr(profile, "auto_pilot_enabled", False),
        "auto_pilot_config":   getattr(profile, "auto_pilot_config", {}),
        "preset_ia_last_run":  profile.preset_ia_last_run.isoformat() if getattr(profile, "preset_ia_last_run", None) else None,
        "preset_ia_config":    getattr(profile, "preset_ia_config", None),
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


# ============================================================================
# PROFILE CRUD
# ============================================================================

@router.get("/")
async def get_profiles(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Get all profiles for current user."""
    query = select(Profile).where(Profile.user_id == user_id).order_by(Profile.created_at.desc())
    result = await db.execute(query)
    profiles = result.scalars().all()
    return {"profiles": [_profile_to_dict(p) for p in profiles]}


@router.get("/{profile_id}")
async def get_profile(
    profile_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Get a single profile by ID, including assigned watchlist."""
    from ..models.profile import WatchlistProfile
    
    query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    result = await db.execute(query)
    profile = result.scalars().first()
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    profile_dict = _profile_to_dict(profile)
    
    # Get assigned watchlist
    wl_query = select(WatchlistProfile).where(
        WatchlistProfile.profile_id == profile_id,
        WatchlistProfile.user_id == user_id
    )
    wl_result = await db.execute(wl_query)
    wl_assignment = wl_result.scalars().first()
    
    if wl_assignment:
        profile_dict["watchlist_id"] = wl_assignment.watchlist_id
        profile_dict["watchlist_profile_type"] = wl_assignment.profile_type
    else:
        profile_dict["watchlist_id"] = None
        profile_dict["watchlist_profile_type"] = None
    
    return profile_dict


@router.post("/")
async def create_profile(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """
    Create a new profile.
    
    Expected payload:
    {
        "name": "High Volume Momentum",
        "description": "Targets high volume coins with strong momentum",
        "config": {
            "filters": {
                "logic": "AND",
                "conditions": [...]
            },
            "scoring": {
                "weights": {...}
            },
            "signals": {
                "logic": "AND",
                "conditions": [...]
            }
        }
    }
    """
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Profile name is required")
    
    # Validate config structure
    config = payload.get("config", {})
    validated_config = _validate_profile_config(config)
    
    profile = Profile(
        user_id=user_id,
        name=name,
        description=payload.get("description", ""),
        is_active=payload.get("is_active", True),
        config=validated_config,
        profile_role=payload.get("profile_role"),
        pipeline_order=str(payload.get("pipeline_order", 99)),
        pipeline_label=payload.get("pipeline_label"),
    )
    
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    
    # If watchlist_id provided, create association
    watchlist_id = payload.get("watchlist_id")
    if watchlist_id:
        from ..models.profile import WatchlistProfile
        # Check if association exists
        existing = await db.execute(
            select(WatchlistProfile).where(
                WatchlistProfile.user_id == user_id,
                WatchlistProfile.watchlist_id == watchlist_id,
                WatchlistProfile.profile_id == profile.id
            )
        )
        if not existing.scalars().first():
            association = WatchlistProfile(
                user_id=user_id,
                watchlist_id=watchlist_id,
                profile_id=profile.id,
                profile_type="L1",  # Default to L1
                is_enabled=True
            )
            db.add(association)
            await db.commit()
    
    return _profile_to_dict(profile)


@router.put("/{profile_id}")
async def update_profile(
    profile_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Update an existing profile."""
    from ..models.profile import WatchlistProfile
    
    query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    result = await db.execute(query)
    profile = result.scalars().first()
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    if "name" in payload:
        profile.name = payload["name"]
    if "description" in payload:
        profile.description = payload["description"]
    if "is_active" in payload:
        profile.is_active = payload["is_active"]
    if "config" in payload:
        profile.config = _validate_profile_config(payload["config"])
    if "profile_role" in payload:
        profile.profile_role = payload["profile_role"]
    if "pipeline_order" in payload:
        profile.pipeline_order = str(payload["pipeline_order"])
    if "pipeline_label" in payload:
        profile.pipeline_label = payload["pipeline_label"]
    if "auto_pilot_enabled" in payload:
        profile.auto_pilot_enabled = payload["auto_pilot_enabled"]
    if "auto_pilot_config" in payload:
        profile.auto_pilot_config = payload["auto_pilot_config"]
    
    # Handle watchlist association
    if "watchlist_id" in payload:
        watchlist_id = payload["watchlist_id"]
        # Remove existing association
        await db.execute(
            delete(WatchlistProfile).where(
                WatchlistProfile.profile_id == profile_id,
                WatchlistProfile.user_id == user_id
            )
        )
        # Create new association if watchlist_id is provided
        if watchlist_id:
            association = WatchlistProfile(
                user_id=user_id,
                watchlist_id=watchlist_id,
                profile_id=profile_id,
                profile_type="L1",  # Default to L1
                is_enabled=True
            )
            db.add(association)
    
    await db.commit()
    await db.refresh(profile)
    
    return _profile_to_dict(profile)


@router.delete("/{profile_id}")
async def delete_profile(
    profile_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Delete a profile."""
    query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    result = await db.execute(query)
    profile = result.scalars().first()
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    # Also remove any watchlist associations
    await db.execute(
        text("DELETE FROM watchlist_profiles WHERE profile_id = :pid"),
        {"pid": str(profile_id)}
    )
    
    await db.delete(profile)
    await db.commit()
    
    return {"status": "success", "message": "Profile deleted"}


# ============================================================================
# PRESET IA + AUTO-PILOT
# ============================================================================

@router.post("/{profile_id}/preset-ia")
async def run_preset_ia(
    profile_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Execute Preset IA: calls Claude and applies optimized config to the profile."""
    result = await db.execute(select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id))
    profile = result.scalars().first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if not getattr(profile, "profile_role", None):
        raise HTTPException(
            status_code=400,
            detail="Profile sem role definido. Configure o papel do profile antes de usar o Preset IA.",
        )

    from ..services.preset_ia_service import run_preset_ia as svc_preset

    try:
        ia_result = await svc_preset(
            profile_id=str(profile_id),
            profile_role=profile.profile_role,
            user_id=user_id,
            current_config=profile.config or {},
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[PresetIA] Unexpected error profile={profile_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao executar Preset IA: {e}")

    # Apply config changes returned by Claude - merge properly into profile.config
    config_changes = ia_result.get("config_changes", {})
    if config_changes:
        merged = dict(profile.config or {})
        
        # Merge filters
        if "filters" in config_changes and config_changes["filters"]:
            if "filters" not in merged:
                merged["filters"] = {"logic": "AND", "conditions": []}
            if "conditions" in config_changes["filters"]:
                merged["filters"]["conditions"] = config_changes["filters"]["conditions"]
            if "logic" in config_changes["filters"]:
                merged["filters"]["logic"] = config_changes["filters"]["logic"]
        
        # Merge scoring
        if "scoring" in config_changes and config_changes["scoring"]:
            if "scoring" not in merged:
                merged["scoring"] = {"enabled": True, "weights": {}}
            if "weights" in config_changes["scoring"]:
                merged["scoring"]["weights"] = config_changes["scoring"]["weights"]
            if "enabled" in config_changes["scoring"]:
                merged["scoring"]["enabled"] = config_changes["scoring"]["enabled"]
        
        # Merge signals
        if "signals" in config_changes and config_changes["signals"]:
            if "signals" not in merged:
                merged["signals"] = {"logic": "AND", "conditions": []}
            if "conditions" in config_changes["signals"]:
                merged["signals"]["conditions"] = config_changes["signals"]["conditions"]
            if "logic" in config_changes["signals"]:
                merged["signals"]["logic"] = config_changes["signals"]["logic"]
        
        profile.config = merged
        logger.info(f"[PresetIA] Config merged for profile={profile_id}: {list(config_changes.keys())}")

    profile.preset_ia_last_run = datetime.now(timezone.utc)
    profile.preset_ia_config = ia_result
    await db.commit()

    return {
        "status":           "success",
        "regime":           ia_result["regime"],
        "macro_risk":       ia_result["macro_risk"],
        "analysis_summary": ia_result["analysis_summary"],
        "applied_configs":  ia_result["applied_configs"],
        "executed_at":      ia_result["executed_at"],
    }


@router.put("/{profile_id}/auto-pilot")
async def update_auto_pilot(
    profile_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Save Auto-Pilot configuration for a profile."""
    result = await db.execute(select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id))
    profile = result.scalars().first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.auto_pilot_enabled = payload.get("enabled", False)
    profile.auto_pilot_config = payload
    await db.commit()
    return {"status": "saved", "enabled": profile.auto_pilot_enabled}


@router.post("/{profile_id}/auto-pilot/trigger")
async def trigger_auto_pilot(
    profile_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Force an immediate Auto-Pilot analysis for this profile."""
    result = await db.execute(select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id))
    profile = result.scalars().first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if not getattr(profile, "auto_pilot_enabled", False):
        raise HTTPException(status_code=400, detail="Auto-Pilot não está ativado para este profile.")

    # Run preset IA immediately (same logic as /preset-ia)
    from ..services.preset_ia_service import run_preset_ia as svc_preset
    try:
        ia_result = await svc_preset(
            profile_id=str(profile_id),
            profile_role=getattr(profile, "profile_role", None) or "primary_filter",
            user_id=user_id,
            current_config=profile.config or {},
            db=db,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    config_changes = ia_result.get("config_changes", {})
    if config_changes:
        merged = dict(profile.config or {})
        for section, changes in config_changes.items():
            if changes:
                non_null = {k: v for k, v in changes.items() if v is not None}
                if non_null:
                    if section in merged and isinstance(merged[section], dict):
                        merged[section].update(non_null)
                    else:
                        merged[section] = non_null
        profile.config = merged

    profile.preset_ia_last_run = datetime.now(timezone.utc)
    profile.preset_ia_config = ia_result
    await db.commit()

    return {"status": "triggered", "regime": ia_result["regime"], "executed_at": ia_result["executed_at"]}


# ============================================================================
# PROFILE TESTING
# ============================================================================

@router.post("/{profile_id}/test")
async def test_profile(
    profile_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """
    Test a profile against current market data.
    
    Returns detailed analysis of how the profile would perform.
    """
    # Get profile
    query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    result = await db.execute(query)
    profile = result.scalars().first()
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    # Get current watchlist data
    assets = await _get_watchlist_assets(db)
    
    if not assets:
        return {
            "profile_id": str(profile_id),
            "profile_name": profile.name,
            "error": "No market data available for testing"
        }
    
    # Run profile engine test
    engine = ProfileEngine(profile.config)
    test_result = engine.test_profile(assets)
    
    return {
        "profile_id": str(profile_id),
        "profile_name": profile.name,
        **test_result
    }


@router.post("/test-config")
async def test_profile_config(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """
    Test a profile configuration without saving.
    
    Useful for validating profile config before creating.
    """
    config = payload.get("config", {})
    
    # Validate config
    try:
        validated_config = _validate_profile_config(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get current watchlist data
    assets = await _get_watchlist_assets(db)
    
    if not assets:
        return {
            "config_valid": True,
            "error": "No market data available for testing"
        }
    
    # Run profile engine test
    engine = ProfileEngine(validated_config)
    test_result = engine.test_profile(assets)
    
    return {
        "config_valid": True,
        **test_result
    }


# ============================================================================
# WATCHLIST-PROFILE ASSIGNMENT
# ============================================================================

@router.post("/watchlist/{watchlist_id}/assign")
async def assign_profile_to_watchlist(
    watchlist_id: str,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """
    Assign a profile to a watchlist.
    
    Payload: {"profile_id": "uuid"}
    """
    profile_id = payload.get("profile_id")
    
    if profile_id:
        # Verify profile exists and belongs to user
        query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
        result = await db.execute(query)
        if not result.scalars().first():
            raise HTTPException(status_code=404, detail="Profile not found")
    
    # Check if assignment already exists
    existing_query = select(WatchlistProfile).where(
        WatchlistProfile.user_id == user_id,
        WatchlistProfile.watchlist_id == watchlist_id
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalars().first()
    
    if existing:
        existing.profile_id = profile_id
        existing.is_enabled = True
    else:
        assignment = WatchlistProfile(
            user_id=user_id,
            watchlist_id=watchlist_id,
            profile_id=profile_id,
            is_enabled=True
        )
        db.add(assignment)
    
    await db.commit()
    
    return {
        "status": "success",
        "watchlist_id": watchlist_id,
        "profile_id": str(profile_id) if profile_id else None
    }


@router.delete("/watchlist/{watchlist_id}/profile")
async def remove_profile_from_watchlist(
    watchlist_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Remove profile assignment from a watchlist."""
    query = select(WatchlistProfile).where(
        WatchlistProfile.user_id == user_id,
        WatchlistProfile.watchlist_id == watchlist_id
    )
    result = await db.execute(query)
    assignment = result.scalars().first()
    
    if assignment:
        await db.delete(assignment)
        await db.commit()
    
    return {"status": "success", "message": "Profile removed from watchlist"}


@router.get("/watchlist/{watchlist_id}/profile")
async def get_watchlist_profile(
    watchlist_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Get the profile assigned to a watchlist."""
    query = select(WatchlistProfile).where(
        WatchlistProfile.user_id == user_id,
        WatchlistProfile.watchlist_id == watchlist_id
    )
    result = await db.execute(query)
    assignment = result.scalars().first()
    
    if not assignment or not assignment.profile_id:
        return {"watchlist_id": watchlist_id, "profile": None, "is_enabled": False}
    
    # Get profile details
    profile_query = select(Profile).where(Profile.id == assignment.profile_id)
    profile_result = await db.execute(profile_query)
    profile = profile_result.scalars().first()
    
    return {
        "watchlist_id": watchlist_id,
        "profile": _profile_to_dict(profile) if profile else None,
        "is_enabled": assignment.is_enabled
    }


@router.put("/watchlist/{watchlist_id}/toggle")
async def toggle_watchlist_profile(
    watchlist_id: str,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Enable/disable profile for a watchlist."""
    query = select(WatchlistProfile).where(
        WatchlistProfile.user_id == user_id,
        WatchlistProfile.watchlist_id == watchlist_id
    )
    result = await db.execute(query)
    assignment = result.scalars().first()
    
    if not assignment:
        raise HTTPException(status_code=404, detail="No profile assigned to this watchlist")
    
    assignment.is_enabled = payload.get("enabled", not assignment.is_enabled)
    await db.commit()
    
    return {
        "watchlist_id": watchlist_id,
        "is_enabled": assignment.is_enabled
    }


# ============================================================================
# HELPERS
# ============================================================================

def _validate_profile_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize profile configuration."""
    validated = {}
    
    # Validate filters
    filters = config.get("filters", {})
    validated["filters"] = {
        "logic": filters.get("logic", "AND").upper(),
        "conditions": filters.get("conditions", [])
    }
    
    # Validate conditions have required fields
    for cond in validated["filters"]["conditions"]:
        if "field" not in cond:
            raise ValueError("Filter condition missing 'field'")
        if "operator" not in cond:
            cond["operator"] = "=="
    
    # Validate scoring weights
    scoring = config.get("scoring", {})
    weights = scoring.get("weights", {})
    validated["scoring"] = {
        "weights": {
            "liquidity": weights.get("liquidity", 25),
            "market_structure": weights.get("market_structure", 25),
            "momentum": weights.get("momentum", 25),
            "signal": weights.get("signal", 25)
        },
        "rules": scoring.get("rules", []),
        "thresholds": scoring.get("thresholds", {
            "strong_buy": 80,
            "buy": 65,
            "neutral": 40
        })
    }
    
    # Validate signals
    signals = config.get("signals", {})
    validated["signals"] = {
        "logic": signals.get("logic", "AND").upper(),
        "conditions": signals.get("conditions", [])
    }
    
    for cond in validated["signals"]["conditions"]:
        if "field" not in cond:
            raise ValueError("Signal condition missing 'field'")
        if "operator" not in cond:
            cond["operator"] = "=="
    
    return validated


async def _get_watchlist_assets(db: AsyncSession) -> List[Dict[str, Any]]:
    """Get current watchlist assets with indicators."""
    try:
        # Get latest scores
        scores_query = text("""
            SELECT DISTINCT ON (symbol)
                symbol, score, liquidity_score, market_structure_score,
                momentum_score, signal_score
            FROM alpha_scores
            ORDER BY symbol, time DESC
        """)
        scores_result = await db.execute(scores_query)
        scores_rows = scores_result.fetchall()
        
        # Get latest indicators
        indicators_query = text("""
            SELECT DISTINCT ON (symbol)
                symbol, indicators_json
            FROM indicators
            ORDER BY symbol, time DESC
        """)
        indicators_result = await db.execute(indicators_query)
        indicators_rows = indicators_result.fetchall()
        
        # Get market metadata
        metadata_query = text("""
            SELECT symbol, name, market_cap, volume_24h, price, price_change_24h
            FROM market_metadata
        """)
        metadata_result = await db.execute(metadata_query)
        metadata_rows = metadata_result.fetchall()
        
        # Build assets list
        scores_map = {r.symbol: r for r in scores_rows}
        indicators_map = {r.symbol: r.indicators_json or {} for r in indicators_rows}
        
        assets = []
        for row in metadata_rows:
            score_row = scores_map.get(row.symbol)
            indicators = indicators_map.get(row.symbol, {})
            
            asset = {
                "symbol": row.symbol,
                "name": row.name,
                "price": float(row.price) if row.price else 0,
                "change_24h": float(row.price_change_24h) if row.price_change_24h else 0,
                "market_cap": float(row.market_cap) if row.market_cap else 0,
                "volume_24h": float(row.volume_24h) if row.volume_24h else 0,
                "indicators": indicators,
                # Flatten some indicators for easier filtering
                **{k: v for k, v in indicators.items() if isinstance(v, (int, float, bool, str))}
            }
            
            if score_row:
                asset["score"] = float(score_row.score) if score_row.score else 0
                asset["liquidity_score"] = float(score_row.liquidity_score) if score_row.liquidity_score else 0
                asset["market_structure_score"] = float(score_row.market_structure_score) if score_row.market_structure_score else 0
                asset["momentum_score"] = float(score_row.momentum_score) if score_row.momentum_score else 0
                asset["signal_score"] = float(score_row.signal_score) if score_row.signal_score else 0
            
            assets.append(asset)
        
        return assets
    except Exception as e:
        logger.warning(f"Failed to get watchlist assets: {e}")
        return []


# ============================================================================
# EXAMPLE PROFILES
# ============================================================================

@router.get("/examples")
async def get_example_profiles(
    user_id: UUID = Depends(get_current_user_id)
):
    """Get example profile configurations for reference."""
    examples = [
        {
            "name": "High Volume Momentum",
            "description": "Targets high volume coins with strong momentum indicators",
            "config": {
                "filters": {
                    "logic": "AND",
                    "conditions": [
                        {"field": "volume_24h", "operator": ">", "value": 10000000},
                        {"field": "atr_percent", "operator": ">", "value": 0.5}
                    ]
                },
                "scoring": {
                    "weights": {
                        "liquidity": 30,
                        "market_structure": 15,
                        "momentum": 40,
                        "signal": 15
                    }
                },
                "signals": {
                    "logic": "AND",
                    "conditions": [
                        {"field": "adx", "operator": ">", "value": 25, "required": True},
                        {"field": "volume_spike", "operator": "==", "value": True, "required": False},
                        {"field": "rsi", "operator": "<", "value": 70, "required": False}
                    ]
                }
            }
        },
        {
            "name": "Oversold Recovery",
            "description": "Finds oversold assets with reversal potential",
            "config": {
                "filters": {
                    "logic": "AND",
                    "conditions": [
                        {"field": "rsi", "operator": "<", "value": 35},
                        {"field": "volume_24h", "operator": ">", "value": 5000000}
                    ]
                },
                "scoring": {
                    "weights": {
                        "liquidity": 25,
                        "market_structure": 25,
                        "momentum": 35,
                        "signal": 15
                    }
                },
                "signals": {
                    "logic": "AND",
                    "conditions": [
                        {"field": "rsi", "operator": "<", "value": 30, "required": True},
                        {"field": "macd_histogram", "operator": ">", "value": 0, "required": False}
                    ]
                }
            }
        },
        {
            "name": "Trend Following",
            "description": "Rides strong trends with EMA alignment",
            "config": {
                "filters": {
                    "logic": "AND",
                    "conditions": [
                        {"field": "adx", "operator": ">", "value": 20},
                        {"field": "ema_full_alignment", "operator": "==", "value": True}
                    ]
                },
                "scoring": {
                    "weights": {
                        "liquidity": 20,
                        "market_structure": 35,
                        "momentum": 30,
                        "signal": 15
                    }
                },
                "signals": {
                    "logic": "AND",
                    "conditions": [
                        {"field": "adx", "operator": ">", "value": 25, "required": True},
                        {"field": "ema9_gt_ema50", "operator": "==", "value": True, "required": True},
                        {"field": "rsi", "operator": ">", "value": 50, "required": False}
                    ]
                }
            }
        }
    ]
    
    return {"examples": examples}
