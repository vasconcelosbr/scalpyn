"""Profiles API — CRUD operations for strategy profiles."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from typing import Dict, Any, List, Optional
from uuid import UUID
import logging
from datetime import datetime, timezone as _tz

from ..database import get_db
from ..models.profile import Profile, WatchlistProfile
from ..models.profile_audit_log import ProfileAuditLog
from .config import get_current_user_id
from ..services.profile_engine import ProfileEngine
from ..services.score_engine import hydrate_profile_scoring
from ..services.config_service import config_service
from ..services.seed_service import DEFAULT_SCORE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profiles", tags=["Profiles"])


async def _hydrate_profile_config_with_global_score(
    db: AsyncSession,
    user_id: UUID,
    profile_config: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not profile_config:
        return profile_config

    global_score_config = DEFAULT_SCORE
    try:
        cfg = await config_service.get_config(db, "score", user_id)
        if cfg and (cfg.get("scoring_rules") or cfg.get("rules")):
            global_score_config = cfg
    except Exception as exc:
        logger.debug("profiles: unable to load global score config: %s", exc)

    return hydrate_profile_scoring(profile_config, global_score_config)


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
        # Profile Intelligence fields (migration 081)
        "profile_type":          getattr(profile, 'profile_type', 'STANDARD'),
        "profile_version": (
            profile.profile_version.isoformat()
            if getattr(profile, 'profile_version', None) else None
        ),
        "is_shadow_only":        getattr(profile, 'is_shadow_only', False),
        "live_trading_enabled":  getattr(profile, 'live_trading_enabled', False),
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


async def _find_duplicate_names(
    db: AsyncSession,
    user_id: UUID,
    name: str,
    exclude_profile_id: Optional[UUID] = None,
) -> List[Dict[str, Any]]:
    """Return profiles owned by user_id with the same name (case-insensitive), excluding a given id."""
    q = select(Profile.id, Profile.name, Profile.created_at).where(
        Profile.user_id == user_id,
        Profile.name.ilike(name),
    )
    if exclude_profile_id:
        q = q.where(Profile.id != exclude_profile_id)
    rows = (await db.execute(q)).fetchall()
    return [{"id": str(r.id), "name": r.name, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


_FUNNEL_ROLE_ORDER: Dict[str, str] = {
    "universe_filter":   "0",
    "primary_filter":    "1",
    "score_engine":      "2",
    "acquisition_queue": "3",
}


def _normalize_import_scoring(
    scoring: Optional[Dict[str, Any]],
    fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = fallback if isinstance(fallback, dict) else {}
    current = scoring if isinstance(scoring, dict) else {}
    merged: Dict[str, Any] = {**base, **current}

    selected_rule_ids = merged.get("selected_rule_ids")
    if selected_rule_ids is not None:
        if not isinstance(selected_rule_ids, list):
            raise ValueError("scoring.selected_rule_ids must be an array")
        merged["selected_rule_ids"] = [str(rule_id) for rule_id in selected_rule_ids if str(rule_id).strip()]

    return merged


@router.post("/bulk-import")
async def bulk_import_profiles(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Create multiple profiles from a JSON import file, or replace scoring for
    every active profile when ``apply_to_active_profiles`` is true.

    Expected payload:
    {
        "profiles": [
            {
                "name": "...",
                "description": "...",          (optional)
                "funnel_role": "acquisition_queue",
                "pipeline_label": "...",       (optional)
                "default_timeframe": "5m",     (optional)
                "filters": {...},              (optional)
                "signals": {...},              (optional)
                "block_rules": {...},          (optional)
                "entry_triggers": {...},       (optional)
                "scoring": {...}               (optional, per-profile)
            }
        ],
        "profile_scoring": {                   (optional, applied to every profile)
            "selected_rule_ids": [...]
        },
        "apply_to_active_profiles": true,      (optional, update existing active profiles)
        "scoring_assignments": [               (optional, update scoring of specific
            {                                   existing profiles by id or name)
                "profile_id": "uuid",          (or "profile_name": "...")
                "selected_rule_ids": [...],    (or nested under "scoring": {...})
                "weights": {...},              (optional)
                "thresholds": {...}            (optional)
            }
        ]
    }
    Returns: { "created": N, "updated": N, "failed": N, "results": [...] }
    """
    results: List[Dict[str, Any]] = []
    created = 0
    failed = 0
    updated = 0
    try:
        shared_scoring = _normalize_import_scoring(
            payload.get("profile_scoring") or payload.get("scoring"),
            {"selected_rule_ids": payload.get("scoring_rule_ids")}
            if isinstance(payload.get("scoring_rule_ids"), list)
            else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    apply_to_active_profiles = bool(
        payload.get("apply_to_active_profiles")
        or payload.get("update_active_profiles")
        or payload.get("active_profiles_only")
    )

    if apply_to_active_profiles:
        selected_rule_ids = shared_scoring.get("selected_rule_ids")
        if selected_rule_ids is None:
            raise HTTPException(
                status_code=400,
                detail="profile_scoring.selected_rule_ids is required when apply_to_active_profiles=true",
            )

        query = select(Profile).where(Profile.user_id == user_id, Profile.is_active.is_(True))
        profiles = (await db.execute(query)).scalars().all()

        for i, profile in enumerate(profiles):
            try:
                previous_config = profile.config or {}
                next_config = {
                    **previous_config,
                    "scoring": _normalize_import_scoring(shared_scoring),
                }
                profile.config = _validate_profile_config(next_config)
                old_version = getattr(profile, "profile_version", None)
                new_version = datetime.now(_tz.utc)
                profile.profile_version = new_version
                db.add(ProfileAuditLog(
                    user_id=user_id,
                    profile_id=profile.id,
                    changed_by=user_id,
                    change_source="api",
                    change_description="scoring replaced via POST /profiles/bulk-import apply_to_active_profiles",
                    previous_config=previous_config,
                    new_config=profile.config,
                    previous_profile_version=old_version,
                    new_profile_version=new_version,
                ))
                results.append({"index": i, "name": profile.name, "status": "updated", "id": str(profile.id)})
                updated += 1
            except Exception as exc:
                failed += 1
                results.append({
                    "index": i,
                    "name": profile.name,
                    "status": "error",
                    "error": str(exc),
                })

        if updated > 0:
            await db.commit()

        return {"created": 0, "updated": updated, "failed": failed, "results": results}

    scoring_assignments = payload.get("scoring_assignments")
    if scoring_assignments is not None and not isinstance(scoring_assignments, list):
        raise HTTPException(status_code=400, detail="'scoring_assignments' must be an array")
    scoring_assignments = scoring_assignments or []
    if len(scoring_assignments) > 200:
        raise HTTPException(status_code=400, detail="Maximum 200 scoring_assignments per import")

    for i, assignment in enumerate(scoring_assignments):
        target_label = ""
        try:
            if not isinstance(assignment, dict):
                raise ValueError("assignment must be an object")

            profile_id = assignment.get("profile_id") or assignment.get("id")
            profile_name = assignment.get("profile_name") or assignment.get("name")
            target_label = str(profile_id or profile_name or f"assignment_{i}")
            if not profile_id and not profile_name:
                raise ValueError("'profile_id' or 'profile_name' is required")

            scoring_input = assignment.get("scoring")
            if not isinstance(scoring_input, dict):
                scoring_input = {
                    key: assignment[key]
                    for key in ("selected_rule_ids", "weights", "thresholds", "enabled")
                    if key in assignment
                }
            merged_scoring = _normalize_import_scoring(scoring_input, shared_scoring)
            if merged_scoring.get("selected_rule_ids") is None:
                raise ValueError("'selected_rule_ids' is required (inline or via profile_scoring)")

            if profile_id:
                try:
                    profile_uuid = UUID(str(profile_id))
                except ValueError:
                    raise ValueError(f"invalid profile_id: {profile_id}")
                q = select(Profile).where(Profile.id == profile_uuid, Profile.user_id == user_id)
                profile = (await db.execute(q)).scalars().first()
            else:
                q = select(Profile).where(Profile.user_id == user_id, Profile.name.ilike(str(profile_name)))
                matches = (await db.execute(q)).scalars().all()
                if len(matches) > 1:
                    raise ValueError(
                        f"{len(matches)} profiles named '{profile_name}' — use profile_id to disambiguate"
                    )
                profile = matches[0] if matches else None

            if not profile:
                raise ValueError("profile not found")

            previous_config = profile.config or {}
            next_config = {**previous_config, "scoring": merged_scoring}
            profile.config = _validate_profile_config(next_config)
            old_version = getattr(profile, "profile_version", None)
            new_version = datetime.now(_tz.utc)
            profile.profile_version = new_version
            db.add(ProfileAuditLog(
                user_id=user_id,
                profile_id=profile.id,
                changed_by=user_id,
                change_source="api",
                change_description="scoring replaced via POST /profiles/bulk-import scoring_assignments",
                previous_config=previous_config,
                new_config=profile.config,
                previous_profile_version=old_version,
                new_profile_version=new_version,
            ))
            results.append({"index": i, "name": profile.name, "status": "updated", "id": str(profile.id)})
            updated += 1
        except Exception as exc:
            failed += 1
            results.append({
                "index": i,
                "name": target_label,
                "status": "error",
                "error": str(exc),
            })

    profiles_data = payload.get("profiles", [])
    if not isinstance(profiles_data, list):
        raise HTTPException(status_code=400, detail="'profiles' must be an array")
    if not profiles_data and not scoring_assignments:
        raise HTTPException(status_code=400, detail="'profiles' array is required")
    if len(profiles_data) > 200:
        raise HTTPException(status_code=400, detail="Maximum 200 profiles per import")

    for i, p in enumerate(profiles_data):
        try:
            name = (p.get("name") or "").strip()
            if not name:
                raise ValueError("'name' is required")

            config_input: Dict[str, Any] = {
                "default_timeframe": p.get("default_timeframe", "5m"),
                "filters":        p.get("filters",        {"logic": "AND", "conditions": []}),
                "signals":        p.get("signals",        {"logic": "AND", "conditions": []}),
                "block_rules":    p.get("block_rules",    {"blocks": []}),
                "entry_triggers": p.get("entry_triggers", {"logic": "AND", "conditions": []}),
                "scoring":        _normalize_import_scoring(p.get("scoring"), shared_scoring),
            }
            validated_config = _validate_profile_config(config_input)

            funnel_role: Optional[str] = p.get("funnel_role")
            profile_role    = funnel_role if funnel_role in _FUNNEL_ROLE_ORDER else None
            pipeline_order  = _FUNNEL_ROLE_ORDER.get(funnel_role) if funnel_role else None  # type: ignore[arg-type]
            pipeline_label  = p.get("pipeline_label") or name

            profile = Profile(
                user_id=user_id,
                name=name,
                description=p.get("description", ""),
                is_active=True,
                config=validated_config,
                profile_role=profile_role,
                pipeline_order=pipeline_order,
                pipeline_label=pipeline_label,
                profile_type="STANDARD",
            )
            db.add(profile)
            await db.flush()

            results.append({"index": i, "name": name, "status": "created", "id": str(profile.id)})
            created += 1

        except Exception as exc:
            failed += 1
            results.append({
                "index":  i,
                "name":   (p.get("name") or f"profile_{i}"),
                "status": "error",
                "error":  str(exc),
            })

    if created > 0 or updated > 0:
        await db.commit()

    return {"created": created, "updated": updated, "failed": failed, "results": results}


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

    # Duplicate name check — non-blocking warning
    duplicates = await _find_duplicate_names(db, user_id, name)
    warnings: List[str] = []
    if duplicates:
        ids_preview = ", ".join(d["id"][:8] + "…" for d in duplicates[:3])
        warnings.append(
            f"Já existe{'m' if len(duplicates) > 1 else ''} {len(duplicates)} profile(s) "
            f"com o nome '{name}': [{ids_preview}]. "
            f"Considere renomear para evitar ambiguidade."
        )

    # Validate config structure
    config = payload.get("config", {})
    try:
        validated_config = _validate_profile_config(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    profile = Profile(
        user_id=user_id,
        name=name,
        description=payload.get("description", ""),
        is_active=payload.get("is_active", True),
        config=validated_config,
        profile_type=payload.get("profile_type", "STANDARD"),
    )

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    result = _profile_to_dict(profile)
    if warnings:
        result["warnings"] = warnings
    return result


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

    _prev_config = profile.config
    _config_changed = False

    rename_warnings: List[str] = []
    if "name" in payload and payload["name"] != profile.name:
        new_name = payload["name"]
        duplicates = await _find_duplicate_names(db, user_id, new_name, exclude_profile_id=profile_id)
        if duplicates:
            ids_preview = ", ".join(d["id"][:8] + "…" for d in duplicates[:3])
            rename_warnings.append(
                f"Já existe{'m' if len(duplicates) > 1 else ''} {len(duplicates)} profile(s) "
                f"com o nome '{new_name}': [{ids_preview}]. "
                f"Considere outro nome para evitar ambiguidade."
            )
        profile.name = new_name
    if "description" in payload:
        profile.description = payload["description"]
    if "is_active" in payload:
        profile.is_active = payload["is_active"]
    if "config" in payload:
        try:
            profile.config = _validate_profile_config(payload["config"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _config_changed = True
    # Salvar papel do profile no pipeline
    if "profile_role" in payload:
        profile.profile_role = payload["profile_role"]
    if "pipeline_order" in payload:
        profile.pipeline_order = str(payload["pipeline_order"]) if payload["pipeline_order"] is not None else "99"

    if _config_changed:
        from datetime import datetime, timezone as _tz
        from ..models.profile_audit_log import ProfileAuditLog
        _old_version = getattr(profile, 'profile_version', None)
        _new_version = datetime.now(_tz.utc)
        profile.profile_version = _new_version
        db.add(ProfileAuditLog(
            user_id=user_id,
            profile_id=profile.id,
            changed_by=user_id,
            change_source="api",
            change_description="config updated via PUT /profiles/{id}",
            previous_config=_prev_config,
            new_config=profile.config,
            previous_profile_version=_old_version,
            new_profile_version=_new_version,
        ))

    await db.commit()
    await db.refresh(profile)

    result = _profile_to_dict(profile)
    if rename_warnings:
        result["warnings"] = rename_warnings
    return result


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
    
    pid = {"pid": str(profile_id)}

    # ── Step 1: profile_intelligence_autopilot_audit ──────────────────────────
    # This table is append-only (immutable trigger blocks all UPDATEs/DELETEs).
    # Two cascade SET NULLs would fire when we later delete candidates and the
    # profile itself — both blocked by the trigger. Disable it, null them out
    # manually up front, then re-enable before proceeding.
    await db.execute(text(
        "ALTER TABLE profile_intelligence_autopilot_audit "
        "DISABLE TRIGGER trg_pi_autopilot_audit_immutable"
    ))
    # candidate_id → SET NULL cascade triggered when we delete candidates below
    await db.execute(text("""
        UPDATE profile_intelligence_autopilot_audit
        SET candidate_id = NULL
        WHERE candidate_id IN (
            SELECT id FROM profile_intelligence_autopilot_candidates
            WHERE profile_id = :pid
        )
    """), pid)
    # profile_id → SET NULL cascade triggered when we delete the profile itself
    await db.execute(
        text("UPDATE profile_intelligence_autopilot_audit SET profile_id = NULL WHERE profile_id = :pid"),
        pid,
    )
    await db.execute(text(
        "ALTER TABLE profile_intelligence_autopilot_audit "
        "ENABLE TRIGGER trg_pi_autopilot_audit_immutable"
    ))

    # ── Step 2: shadow_trades ─────────────────────────────────────────────────
    # shadow_trades.profile_id is SET NULL, but ux_shadow_running_user_source
    # (narrowed in migration c003 to WHERE profile_id IS NULL AND completed_at
    # IS NULL) could still conflict if there are running baseline trades for the
    # same (user, symbol, source).  Close any running trades for this profile
    # first so they exit the unique-index scope before cascade sets profile_id=NULL.
    await db.execute(text("""
        UPDATE shadow_trades
        SET completed_at = NOW()
        WHERE profile_id = :pid AND completed_at IS NULL
    """), pid)

    # ── Step 3: RESTRICT-FK tables ────────────────────────────────────────────
    await db.execute(text(
        "DELETE FROM profile_intelligence_autopilot_candidates WHERE profile_id = :pid"
    ), pid)
    await db.execute(text(
        "DELETE FROM autopilot_pending_actions WHERE profile_id = :pid"
    ), pid)
    # profile_adjustment_versions has CASCADE from profile_adjustment_suggestions;
    # delete versions first to satisfy its own RESTRICT FK on profiles.id.
    await db.execute(text(
        "DELETE FROM profile_adjustment_versions WHERE profile_id = :pid"
    ), pid)
    await db.execute(text(
        "DELETE FROM profile_adjustment_suggestions WHERE profile_id = :pid"
    ), pid)

    # ── Step 4: delete profile (remaining SET NULL cascades fire here) ────────
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
    hydrated_profile_config = await _hydrate_profile_config_with_global_score(db, user_id, profile.config)
    engine = ProfileEngine(hydrated_profile_config)
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
    hydrated_profile_config = await _hydrate_profile_config_with_global_score(db, user_id, validated_config)
    engine = ProfileEngine(hydrated_profile_config)
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
    validated = {
        "default_timeframe": config.get("default_timeframe", "5m")
    }

    # Validate filters
    filters = config.get("filters", {})
    validated["filters"] = {
        "logic": filters.get("logic", "AND").upper(),
        "conditions": filters.get("conditions", [])
    }

    for cond in validated["filters"]["conditions"]:
        field = cond.get("field") or cond.get("indicator")
        if not field:
            raise ValueError("Filter condition missing 'field'")
        cond["field"] = field
        cond.pop("indicator", None)
        if "operator" not in cond:
            cond["operator"] = "=="

    # Validate scoring weights
    scoring = config.get("scoring", {})
    weights = scoring.get("weights", {})
    validated["scoring"] = {
        "enabled": scoring.get("enabled", True),
        "weights": {
            "liquidity": weights.get("liquidity", 25),
            "market_structure": weights.get("market_structure", 25),
            "momentum": weights.get("momentum", 25),
            "signal": weights.get("signal", 25)
        },
        "rules": scoring.get("rules", []),
        # New: explicit list of global scoring rule IDs activated for this profile
        # (set via the Scoring tab, decoupled from filter conditions)
        "selected_rule_ids": scoring.get("selected_rule_ids", []),
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
        field = cond.get("field") or cond.get("indicator")
        if not field:
            raise ValueError("Signal condition missing 'field'")
        cond["field"] = field
        cond.pop("indicator", None)
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
                        {"field": "volume_spike", "operator": ">=", "value": 2.0, "required": False},
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
