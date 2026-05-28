"""
Auto-Pilot API endpoints.

Base prefix: /api/autopilot

Routes:
  GET    /{profile_id}/status           — estado atual + métricas recentes
  GET    /{profile_id}/history          — log de auditorias + versões
  POST   /{profile_id}/rollback/{version_id}  — restaurar versão anterior
  POST   /{profile_id}/run              — executar ciclo manualmente (on-demand)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.profile import Profile
from .config import get_current_user_id

logger = logging.getLogger("scalpyn.api.autopilot")

router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/{profile_id}/status")
async def get_autopilot_status(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Retorna o estado atual do Auto-Pilot para um profile."""
    profile = await _get_profile(profile_id, user_id, db)

    ap_config = dict(getattr(profile, "auto_pilot_config", None) or {})

    # Performance recente (últimos 30 dias) — somente se não houver erro
    perf = None
    try:
        from ..services.autopilot_engine import compute_performance_window
        perf = await compute_performance_window(days=30, db=db)
    except Exception as e:
        logger.warning(f"[Autopilot API] Falha ao computar performance para status: {e}")

    # Última mutação
    last_mutation_at = ap_config.get("last_mutation_at")
    circuit_broken = False
    circuit_until = None
    consecutive_regressions = ap_config.get("consecutive_regressions", 0)
    paused_at = ap_config.get("circuit_breaker_paused_at")
    if consecutive_regressions >= 3 and paused_at:
        from datetime import datetime, timedelta, timezone
        try:
            pa = datetime.fromisoformat(paused_at)
            pause_until = pa + timedelta(hours=168)
            if datetime.now(timezone.utc) < pause_until:
                circuit_broken = True
                circuit_until = pause_until.isoformat()
        except Exception:
            pass

    return {
        "profile_id":              profile_id,
        "profile_name":            profile.name,
        "profile_role":            getattr(profile, "profile_role", None),
        "auto_pilot_enabled":      getattr(profile, "auto_pilot_enabled", False),
        "last_mutation_at":        last_mutation_at,
        "last_regime":             ap_config.get("last_regime"),
        "last_mutation_reason":    ap_config.get("mutation_reason"),
        "last_analysis_summary":   ap_config.get("analysis_summary"),
        "macro_risk":              ap_config.get("macro_risk"),
        "consecutive_regressions": consecutive_regressions,
        "circuit_breaker_active":  circuit_broken,
        "circuit_breaker_until":   circuit_until,
        "ev_before_last_mutation": ap_config.get("ev_before_last_mutation"),
        "ev_after_last_mutation":  ap_config.get("ev_after_last_mutation"),
        "performance":             perf,
    }


# ── History ───────────────────────────────────────────────────────────────────

@router.get("/{profile_id}/history")
async def get_autopilot_history(
    profile_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Retorna histórico de decisões e versões do Auto-Pilot para um profile."""
    await _get_profile(profile_id, user_id, db)  # ownership check

    # Audit logs
    audit_result = await db.execute(text("""
        SELECT id, action, reason, regime, perf_snapshot, version_id, created_at
        FROM autopilot_audit_logs
        WHERE profile_id = :pid
        ORDER BY created_at DESC
        LIMIT :lim
    """), {"pid": profile_id, "lim": limit})
    audit_rows = [dict(r) for r in audit_result.mappings().all()]

    # Version history
    ver_result = await db.execute(text("""
        SELECT id, version_number, regime, ev_at_snapshot, win_rate_at_snapshot,
               fpr_at_snapshot, n_samples, mutation_reason, created_at
        FROM profile_versions
        WHERE profile_id = :pid
        ORDER BY version_number DESC
        LIMIT :lim
    """), {"pid": profile_id, "lim": limit})
    ver_rows = [dict(r) for r in ver_result.mappings().all()]

    # Serialize UUIDs and datetimes
    def _serialize(rows: list) -> list:
        out = []
        for row in rows:
            d = {}
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
                else:
                    d[k] = str(v) if hasattr(v, "hex") else v
            out.append(d)
        return out

    return {
        "profile_id":    profile_id,
        "audit_logs":    _serialize(audit_rows),
        "versions":      _serialize(ver_rows),
    }


# ── Rollback ──────────────────────────────────────────────────────────────────

@router.post("/{profile_id}/rollback/{version_id}")
async def rollback_autopilot(
    profile_id: str,
    version_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Restaura a config do profile para uma versão anterior do Auto-Pilot."""
    from ..services.autopilot_engine import rollback_to_version

    profile = await _get_profile(profile_id, user_id, db)

    try:
        result = await rollback_to_version(
            profile_id=profile_id,
            version_id=version_id,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Apply restored config to profile
    profile.config = result["config"]
    # Reset regression counter on manual rollback
    ap_config = dict(getattr(profile, "auto_pilot_config", None) or {})
    ap_config["consecutive_regressions"] = 0
    ap_config.pop("circuit_breaker_paused_at", None)
    profile.auto_pilot_config = ap_config

    from datetime import datetime, timezone
    profile.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "status":         "success",
        "profile_id":     profile_id,
        "version_number": result["version_number"],
        "regime":         result.get("regime"),
        "ev_at_snapshot": result.get("ev_at_snapshot"),
        "message":        f"Config restaurada para versão {result['version_number']}. Circuit breaker resetado.",
    }


# ── Manual Trigger ────────────────────────────────────────────────────────────

@router.post("/{profile_id}/run")
async def run_autopilot_now(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Executa o ciclo de Auto-Pilot manualmente para um profile (on-demand).
    Ignora o cooldown de MIN_HOURS_BETWEEN_MUTATIONS.
    """
    from datetime import datetime, timezone
    from ..services.autopilot_engine import run_autopilot_cycle

    profile = await _get_profile(profile_id, user_id, db)

    if not profile.profile_role:
        raise HTTPException(
            status_code=400,
            detail="Profile sem role definido. Configure o role antes de usar o Auto-Pilot.",
        )

    current_config = dict(profile.config or {})
    # For manual trigger: override cooldown by clearing last_mutation_at temporarily
    ap_config = dict(getattr(profile, "auto_pilot_config", None) or {})
    ap_config_override = dict(ap_config)
    ap_config_override.pop("last_mutation_at", None)  # bypass cooldown

    cycle_result = await run_autopilot_cycle(
        profile_id=profile_id,
        profile_role=profile.profile_role,
        user_id=str(user_id),
        current_config=current_config,
        auto_pilot_config=ap_config_override,
        db=db,
    )

    # Apply mutation if needed
    if cycle_result.get("action") == "MUTATED":
        profile.config = cycle_result["new_config"]
        profile.auto_pilot_config = cycle_result["updated_ap_config"]
        profile.updated_at = datetime.now(timezone.utc)
        await db.commit()

    return {
        "status":           "success",
        "profile_id":       profile_id,
        "action":           cycle_result.get("action"),
        "dry_run":          cycle_result.get("dry_run", True),
        "reason":           cycle_result.get("reason"),
        "regime":           cycle_result.get("regime"),
        "analysis_summary": cycle_result.get("analysis_summary"),
        "performance":      cycle_result.get("perf"),
        "rule_adjustment":  cycle_result.get("rule_adjustment"),
        "proposed_config":  cycle_result.get("proposed_config"),  # apenas em DRY_RUN_MUTATED
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_profile(
    profile_id: str,
    user_id: UUID,
    db: AsyncSession,
) -> Profile:
    result = await db.execute(
        select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile não encontrado")
    return profile
