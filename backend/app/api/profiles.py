"""Profiles API — CRUD operations for strategy profiles."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from typing import Dict, Any, List, Optional
from uuid import UUID
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
        # Campos necessários para Preset IA e pipeline
        "profile_role":   getattr(profile, 'profile_role', None),
        "pipeline_order": getattr(profile, 'pipeline_order', None),
        "preset_ia_last_run": (
            profile.preset_ia_last_run.isoformat()
            if getattr(profile, 'preset_ia_last_run', None) else None
        ),
        "preset_ia_config": getattr(profile, 'preset_ia_config', None),
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
    """Get a single profile by ID."""
    query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    result = await db.execute(query)
    profile = result.scalars().first()
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    return _profile_to_dict(profile)


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
        config=validated_config
    )
    
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    
    return _profile_to_dict(profile)


@router.put("/{profile_id}")
async def update_profile(
    profile_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id)
):
    """Update an existing profile."""
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
    # Salvar papel do profile no pipeline
    if "profile_role" in payload:
        profile.profile_role = payload["profile_role"]
    if "pipeline_order" in payload:
        profile.pipeline_order = str(payload["pipeline_order"]) if payload["pipeline_order"] is not None else "99"
    
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

    # Validate signals (L3 entry conditions)
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

    # Preserve block_rules (blocking conditions that prevent a buy)
    block_rules = config.get("block_rules", {})
    validated["block_rules"] = {
        "blocks": block_rules.get("blocks", [])
    }

    # Preserve entry_triggers (explicit entry conditions)
    entry_triggers = config.get("entry_triggers", {})
    validated["entry_triggers"] = {
        "logic": entry_triggers.get("logic", "AND").upper(),
        "conditions": entry_triggers.get("conditions", [])
    }

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


# ============================================================================
# FILTERED ASSETS (para Watchlist)
# ============================================================================

@router.get("/{profile_id}/filtered-assets")
async def get_filtered_assets(
    profile_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Executa o ProfileEngine com os filtros e scoring do profile e retorna
    os assets que passaram, ordenados por score descendente.
    Usado pela Watchlist para exibir criptos filtradas pelo profile associado.
    """
    query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    result = await db.execute(query)
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile não encontrado")

    assets = await _get_watchlist_assets(db)
    if not assets:
        return {
            "profile_id": profile_id,
            "profile_name": profile.name,
            "profile_role": getattr(profile, 'profile_role', None),
            "total_universe": 0,
            "after_filter": 0,
            "assets": [],
            "error": "Sem dados de mercado disponíveis. Execute a descoberta de ativos primeiro.",
        }

    engine = ProfileEngine(profile.config or {})
    test_result = engine.test_profile(assets)

    # Enriquecer assets com dados de mercado
    filtered = test_result.get("filtered_assets", []) or []
    # Limitar e ordenar por score
    filtered_sorted = sorted(
        filtered,
        key=lambda a: (a.get("score", {}) or {}).get("total_score", 0),
        reverse=True
    )[:limit]

    return {
        "profile_id": profile_id,
        "profile_name": profile.name,
        "profile_role": getattr(profile, 'profile_role', None),
        "total_universe": test_result.get("summary", {}).get("total_assets", len(assets)),
        "after_filter": test_result.get("summary", {}).get("after_filter", len(filtered)),
        "filter_rate": test_result.get("summary", {}).get("filter_rate", "0%"),
        "signals_triggered": test_result.get("summary", {}).get("signals_triggered", 0),
        "assets": filtered_sorted,
    }


# ============================================================================
# PRESET IA
# ============================================================================

@router.post("/{profile_id}/preset-ia")
async def run_preset_ia(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Executa o Preset IA para o profile.
    1. Carrega config atual do profile
    2. Coleta snapshot de mercado
    3. Chama Claude com system prompt do role
    4. Valida o retorno
    5. Salva em profile.config
    6. Retorna resultado para o frontend atualizar a UI
    """
    from datetime import datetime, timezone
    from ..services.preset_ia_service import run_preset_ia as svc_preset

    # Buscar profile
    query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    result = await db.execute(query)
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile não encontrado")

    if not profile.profile_role:
        raise HTTPException(
            status_code=400,
            detail="Profile sem role definido. Selecione o papel do profile antes de usar o Preset IA.",
        )

    current_config = profile.config or {}

    # Executar Preset IA
    ia_result = await svc_preset(
        profile_id=profile_id,
        profile_role=profile.profile_role,
        user_id=str(user_id),
        current_profile_config=current_config,
        db=db,
    )

    # Salvar resultado no profile
    profile.config = ia_result["config"]
    profile.preset_ia_last_run = datetime.now(timezone.utc)
    profile.preset_ia_config = {
        "regime":           ia_result["regime"],
        "macro_risk":       ia_result["macro_risk"],
        "analysis_summary": ia_result["analysis_summary"],
        "executed_at":      ia_result["executed_at"],
    }
    profile.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(profile)

    return {
        "status":           "success",
        "regime":           ia_result["regime"],
        "macro_risk":       ia_result["macro_risk"],
        "analysis_summary": ia_result["analysis_summary"],
        "config":           ia_result["config"],
        "profile":          _profile_to_dict(profile),
        "executed_at":      ia_result["executed_at"],
    }


# ============================================================================
# AUTO-PILOT TOGGLE
# ============================================================================

@router.post("/{profile_id}/autopilot/toggle")
async def toggle_profile_autopilot(
    profile_id: str,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Liga/desliga o Auto-Pilot do profile."""
    query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    result = await db.execute(query)
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile não encontrado")

    enabled = payload.get("enabled")
    if enabled is None:
        current = getattr(profile, "auto_pilot_enabled", False) or False
        enabled = not current

    # O campo no banco é auto_pilot_enabled (migration 009)
    if hasattr(profile, "auto_pilot_enabled"):
        profile.auto_pilot_enabled = bool(enabled)
    await db.commit()
    await db.refresh(profile)

    response = _profile_to_dict(profile)
    response["autopilot_enabled"] = getattr(profile, "auto_pilot_enabled", False)
    return {"status": "success", "profile": response}
