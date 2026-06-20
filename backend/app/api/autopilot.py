"""
Auto-Pilot API endpoints.

Base prefix: /api/autopilot

Routes:
  GET    /{profile_id}/status           — estado atual + métricas recentes
  GET    /{profile_id}/history          — log de auditorias + versões
  POST   /{profile_id}/rollback/{version_id}  — restaurar versão anterior
  POST   /{profile_id}/run              — executar ciclo manualmente (on-demand)
  GET    /{profile_id}/skills           — skills disponíveis + performance
  GET    /{profile_id}/regime           — regime de mercado atual + histórico
  POST   /{profile_id}/skill/{skill_key} — selecionar skill manualmente
  POST   /{profile_id}/backtest         — backtest comparativo
"""

from __future__ import annotations

import logging
from typing import Any, Dict
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
        perf = await compute_performance_window(
            days=30,
            db=db,
            user_id=str(user_id),
            profile_id=profile_id,
            mutation_context=False,
        )
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

    # ── Market Skills info ──
    active_skill_key = ap_config.get("manual_skill_key")
    skill_mode = ap_config.get("skill_mode", "auto")
    current_regime = None
    try:
        from ..services.market_regime_engine import MarketRegimeEngine
        _engine = MarketRegimeEngine()
        _regime_signal = await _engine.detect_global_regime(db)
        current_regime = _regime_signal.regime.value if hasattr(_regime_signal.regime, 'value') else str(_regime_signal.regime)
    except Exception as e:
        logger.warning(f"[Autopilot API] Falha ao detectar regime para status: {e}")

    return {
        "profile_id":              profile_id,
        "profile_name":            profile.name,
        "profile_role":            getattr(profile, "profile_role", None),
        "auto_pilot_enabled":      getattr(profile, "auto_pilot_enabled", False),
        "last_mutation_at":        last_mutation_at,
        "last_regime":             ap_config.get("last_regime"),
        "current_regime":          current_regime,
        "last_mutation_reason":    ap_config.get("mutation_reason"),
        "last_analysis_summary":   ap_config.get("analysis_summary"),
        "macro_risk":              ap_config.get("macro_risk"),
        "consecutive_regressions": consecutive_regressions,
        "circuit_breaker_active":  circuit_broken,
        "circuit_breaker_until":   circuit_until,
        "ev_before_last_mutation": ap_config.get("ev_before_last_mutation"),
        "ev_after_last_mutation":  ap_config.get("ev_after_last_mutation"),
        "performance":             perf,
        "skill_mode":              skill_mode,
        "active_skill_key":        active_skill_key,
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
        SELECT id, user_id, profile_id, action, reason, reason_code, regime,
               target_config, target_section, perf_snapshot, performance_window,
               evidence_count, config_before, config_after, diff_json,
               mutation_applied, version_id, trigger_source, celery_task_id,
               profile_name, created_at
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
        trigger_source="manual_api",
        profile_name=getattr(profile, "name", None),
    )

    return {
        "status":           cycle_result.get("status", "success"),
        "profile_id":       profile_id,
        "action":           cycle_result.get("action"),
        "mutation_applied": cycle_result.get("mutation_applied", False),
        "autopilot_still_active": cycle_result.get("autopilot_still_active", True),
        "dry_run":          cycle_result.get("dry_run", True),
        "reason":           cycle_result.get("reason"),
        "regime":           cycle_result.get("regime"),
        "analysis_summary": cycle_result.get("analysis_summary"),
        "performance":      cycle_result.get("perf"),
        "rule_adjustment":  cycle_result.get("rule_adjustment"),
        "proposed_config":  cycle_result.get("proposed_config"),  # apenas em DRY_RUN_MUTATED
    }


# ── Market Skills Engine ──────────────────────────────────────────────────────

@router.get("/{profile_id}/skills")
async def get_autopilot_skills(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Lista os Skills disponíveis e sua performance por regime."""
    from ..services.skill_profiles import load_user_skills, seed_user_skills
    from ..services.market_regime_engine import MarketRegimeEngine

    await _get_profile(profile_id, user_id, db)

    # Seed defaults if needed
    skills = await load_user_skills(db, str(user_id))
    if not skills or len(skills) == 0:
        await seed_user_skills(db, str(user_id))
        skills = await load_user_skills(db, str(user_id))

    # Get current regime
    engine = MarketRegimeEngine()
    regime_signal = await engine.detect_global_regime(db)

    skills_list = []
    for key, skill in skills.items():
        affinity_match = regime_signal.regime in skill.regime_affinity
        skills_list.append({
            "skill_key": key,
            "name": skill.name,
            "description": skill.description,
            "regime_affinity": [r.value for r in skill.regime_affinity],
            "affinity_match": affinity_match,
            "scoring_thresholds": skill.scoring_thresholds,
            "n_scoring_rules": len(skill.scoring_rules),
            "n_block_rules": len(skill.block_rules),
            "performance_history": skill.performance_history,
        })

    return {
        "profile_id": profile_id,
        "current_regime": regime_signal.to_dict(),
        "skills": skills_list,
    }


@router.get("/{profile_id}/regime")
async def get_current_regime(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Retorna o regime de mercado atual com detalhes."""
    from ..services.market_regime_engine import MarketRegimeEngine

    _ = await _get_profile(profile_id, user_id, db)  # auth check

    engine = MarketRegimeEngine()
    signal = await engine.detect_global_regime(db)

    # Get regime history (last 10)
    history_result = await db.execute(text("""
        SELECT regime, confidence, source, detected_at
        FROM regime_history
        ORDER BY detected_at DESC
        LIMIT 10
    """))
    history = [
        {
            "regime": r.regime,
            "confidence": r.confidence,
            "source": r.source,
            "detected_at": r.detected_at.isoformat() if hasattr(r.detected_at, 'isoformat') else str(r.detected_at),
        }
        for r in history_result.mappings().all()
    ]

    return {
        "current": signal.to_dict(),
        "history": history,
    }


@router.post("/{profile_id}/skill/{skill_key}")
async def set_manual_skill(
    profile_id: str,
    skill_key: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Seleciona um skill manualmente para o profile."""
    from ..services.skill_profiles import load_user_skills, get_skill_template

    profile = await _get_profile(profile_id, user_id, db)
    skills = await load_user_skills(db, str(user_id))

    skill = skills.get(skill_key) or get_skill_template(skill_key)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_key}' não encontrado.")

    # Update profile auto_pilot_config with manual skill
    ap_config = dict(getattr(profile, 'auto_pilot_config', None) or {})
    ap_config['skill_mode'] = 'manual'
    ap_config['manual_skill_key'] = skill_key
    profile.auto_pilot_config = ap_config

    from datetime import datetime, timezone
    profile.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "status": "success",
        "profile_id": profile_id,
        "skill_key": skill_key,
        "skill_name": skill.name,
        "mode": "manual",
    }


# ── Backtest ──────────────────────────────────────────────────────────────────

@router.post("/{profile_id}/backtest")
async def run_backtest(
    profile_id: str,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Executa backtest comparativo: regras atuais vs Market Skills Engine."""
    from ..services.backtest_skills import run_skills_backtest

    _ = await _get_profile(profile_id, user_id, db)  # auth check

    try:
        result = await run_skills_backtest(
            db=db,
            user_id=str(user_id),
            days=days,
            limit=500,
        )
        return {
            "status": "success",
            "profile_id": profile_id,
            "backtest": result,
        }
    except Exception as exc:
        return {
            "status": "error",
            "profile_id": profile_id,
            "error": str(exc),
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
