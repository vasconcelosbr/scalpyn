"""Profile Intelligence Engine API."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.profile_intelligence import (
    ProfileIntelligenceRun,
    ProfileIndicatorStats,
    ProfileRuleCombination,
    ProfileSuggestion,
    ProfileIntelligenceAuditLog,
)
from .config import get_current_user_id
from ..schemas.profile_intelligence import (
    RunRequest,
    RunResponse,
    PISettingsUpdate,
    CreateProfileRequest,
    AutopilotSettingsUpdate,
)
from ..services.profile_intelligence_audit_service import log_pi_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile-intelligence", tags=["profile-intelligence"])

_DEFAULT_SETTINGS = {
    "min_support": 0.05,
    "min_closed_trades": 30,
    "min_lift": 1.1,
    "min_win_rate": 0.45,
    "max_avg_mae": -3.0,
    "max_avg_holding_seconds": 7200,
    "required_tp_30m_rate": 0.20,
    "max_combinations_per_run": 500,
    "enable_anthropic_explanations": False,
    "enable_optuna": False,
    "enable_association_rules": False,
    "enable_dynamic_combinations": True,
    "enable_lightgbm": False,
    "enable_catboost": False,
}


# ── 1. Overview ──────────────────────────────────────────────────────────────

@router.get("/overview")
async def get_overview(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Returns a summary of the last PI Engine run for the current user."""
    uid_str = str(user_id)

    # Last run
    run_result = await db.execute(
        select(ProfileIntelligenceRun)
        .where(ProfileIntelligenceRun.user_id == user_id)
        .order_by(ProfileIntelligenceRun.run_at.desc())
        .limit(1)
    )
    last_run = run_result.scalars().first()

    # Total runs
    total_runs_count = (await db.execute(text(
        "SELECT COUNT(*) FROM profile_intelligence_runs WHERE user_id = :uid"
    ), {"uid": uid_str})).scalar() or 0

    # Pending suggestions count
    pending_count = (await db.execute(text("""
        SELECT COUNT(*) FROM profile_suggestions
        WHERE user_id = :uid AND status = 'pending_user_approval'
    """), {"uid": uid_str})).scalar() or 0

    # High-confidence suggestions
    high_conf_count = (await db.execute(text("""
        SELECT COUNT(*) FROM profile_suggestions
        WHERE user_id = :uid AND confidence_level = 'HIGH'
          AND status NOT IN ('rejected', 'archived')
    """), {"uid": uid_str})).scalar() or 0

    # Total combinations (all-time)
    total_combos_count = (await db.execute(text(
        "SELECT COUNT(*) FROM profile_rule_combinations WHERE user_id = :uid"
    ), {"uid": uid_str})).scalar() or 0

    # Combinations count (not yet shadow-tested)
    untested_count = (await db.execute(text("""
        SELECT COUNT(*) FROM profile_rule_combinations
        WHERE user_id = :uid AND is_tested_live_shadow = false
    """), {"uid": uid_str})).scalar() or 0

    # Best combination
    best_combo_result = await db.execute(
        select(ProfileRuleCombination)
        .where(ProfileRuleCombination.user_id == user_id)
        .order_by(ProfileRuleCombination.champion_score.desc().nullslast())
        .limit(1)
    )
    best_combo = best_combo_result.scalars().first()

    # Best profile (top win rate, min 30 closed trades, last 60 days)
    best_profile_row = (await db.execute(text("""
        SELECT
            profile_name,
            COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) AS closed,
            COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS wins
        FROM shadow_trades
        WHERE user_id = :uid
          AND profile_id IS NOT NULL
          AND created_at >= NOW() - INTERVAL '60 days'
        GROUP BY profile_name
        HAVING COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) >= 30
        ORDER BY
            COUNT(*) FILTER (WHERE outcome = 'TP_HIT')::float /
            GREATEST(COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')),1) DESC
        LIMIT 1
    """), {"uid": uid_str})).fetchone()

    # ML availability
    ml_available = {}
    try:
        import xgboost; ml_available["xgboost"] = True
    except ImportError: ml_available["xgboost"] = False
    try:
        import lightgbm; ml_available["lightgbm"] = True
    except ImportError: ml_available["lightgbm"] = False
    try:
        import optuna; ml_available["optuna"] = True
    except ImportError: ml_available["optuna"] = False
    try:
        import mlxtend; ml_available["mlxtend"] = True
    except ImportError: ml_available["mlxtend"] = False
    try:
        import shap; ml_available["shap"] = True
    except ImportError: ml_available["shap"] = False
    try:
        import anthropic; ml_available["anthropic_sdk"] = True
    except ImportError: ml_available["anthropic_sdk"] = False

    best_profile_wr = (
        float(best_profile_row.wins) / max(int(best_profile_row.closed), 1)
        if best_profile_row else None
    )

    return {
        # Flat fields for PIOverview interface
        "total_runs": total_runs_count,
        "last_run_at": last_run.run_at.isoformat() if last_run else None,
        "last_run_status": last_run.status if last_run else None,
        "total_profiles_analyzed": last_run.total_profiles if last_run else 0,
        "total_closed_trades": last_run.total_closed_trades if last_run else 0,
        "total_combinations": total_combos_count,
        "total_suggestions_pending": pending_count,
        "total_suggestions_high_confidence": high_conf_count,
        "base_win_rate": float(last_run.base_win_rate or 0) if last_run else None,
        "best_profile_name": best_profile_row.profile_name if best_profile_row else None,
        "best_profile_win_rate": round(best_profile_wr, 4) if best_profile_wr is not None else None,
        "best_combination_name": best_combo.suggested_name if best_combo else None,
        "best_combination_champion_score": float(best_combo.champion_score or 0) if best_combo else None,
        # Nested data kept for backward compat
        "last_run": _run_to_dict(last_run) if last_run else None,
        "pending_suggestions": pending_count,
        "untested_combinations": untested_count,
        "best_combination": {
            "id": str(best_combo.id),
            "suggested_name": best_combo.suggested_name,
            "champion_score": float(best_combo.champion_score or 0),
            "confidence_level": best_combo.confidence_level,
            "setup_family": best_combo.setup_family,
        } if best_combo else None,
        "ml_availability": ml_available,
    }


# ── 2. List runs ──────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    q = select(ProfileIntelligenceRun).where(ProfileIntelligenceRun.user_id == user_id)
    if status:
        q = q.where(ProfileIntelligenceRun.status == status)
    q = q.order_by(ProfileIntelligenceRun.run_at.desc()).limit(limit)
    runs = (await db.execute(q)).scalars().all()
    return {"runs": [_run_to_dict(r) for r in runs]}


# ── 3. Trigger run ────────────────────────────────────────────────────────────

@router.post("/run", response_model=RunResponse)
async def trigger_run(
    payload: RunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> RunResponse:
    """Trigger a new PI Engine run. Returns run_id immediately; executes in background."""
    from ..services.profile_intelligence_service import ProfileIntelligenceService
    svc = ProfileIntelligenceService()

    # Create the run record immediately so we can return the ID
    run = ProfileIntelligenceRun(
        user_id=user_id,
        lookback_days=payload.lookback_days,
        min_closed_trades=payload.min_closed_trades,
        status="queued",
        engine_version=ProfileIntelligenceService.ENGINE_VERSION,
        settings_json=payload.settings_override or {},
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    run_id = run.id

    # Queue background execution
    from ..database import AsyncSessionLocal
    async def _run_background():
        async with AsyncSessionLocal() as bg_db:
            try:
                await svc.run(
                    db=bg_db,
                    user_id=user_id,
                    run_id=run_id,
                    lookback_days=payload.lookback_days,
                    min_closed_trades=payload.min_closed_trades,
                    include_counterfactual=payload.include_counterfactual,
                    include_dynamic_combinations=payload.include_dynamic_combinations,
                    include_association_rules=payload.include_association_rules,
                    include_optuna=payload.include_optuna,
                    include_ai_explanation=payload.include_ai_explanation,
                    profiles_filter=payload.profiles_filter,
                    max_combinations=payload.max_combinations,
                    settings_override=payload.settings_override,
                )
            except Exception as exc:
                logger.error("[PI API] Background run %s failed: %s", run_id, exc)
                try:
                    await bg_db.execute(
                        update(ProfileIntelligenceRun)
                        .where(ProfileIntelligenceRun.id == run_id)
                        .values(status="failed", error_message=str(exc)[:500])
                    )
                    await bg_db.commit()
                except Exception:
                    pass

    background_tasks.add_task(_run_background)

    return RunResponse(run_id=str(run_id), status="queued", message="Run queued successfully")


# ── 4. Profile ranking ────────────────────────────────────────────────────────

@router.get("/profiles/ranking")
async def get_profile_ranking(
    lookback_days: int = Query(default=60, ge=7, le=365),
    min_closed_trades: int = Query(default=10, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    rows = (await db.execute(text(f"""
        SELECT
            profile_id,
            profile_name,
            source,
            COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) as closed_trades,
            COUNT(*) FILTER (WHERE outcome = 'TP_HIT') as wins,
            COUNT(*) FILTER (WHERE outcome = 'SL_HIT') as losses,
            COUNT(*) FILTER (WHERE outcome = 'TIMEOUT') as timeouts,
            ROUND(AVG(pnl_pct) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT'))::numeric, 4) as avg_pnl_pct,
            ROUND(AVG(mae_pct)::numeric, 4) as avg_mae_pct,
            COUNT(*) FILTER (WHERE outcome='TP_HIT' AND holding_seconds <= 1800) as tp_30m,
            COUNT(*) as total_trades
        FROM shadow_trades
        WHERE user_id = :uid
          AND created_at >= NOW() - INTERVAL '{lookback_days} days'
          AND profile_id IS NOT NULL
        GROUP BY profile_id, profile_name, source
        HAVING COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) >= :min_ct
        ORDER BY wins::float / GREATEST(COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')),1) DESC
        LIMIT :limit
    """), {"uid": str(user_id), "min_ct": min_closed_trades, "limit": limit})).fetchall()

    profiles = []
    for r in rows:
        closed = r.closed_trades or 0
        wins = r.wins or 0
        tp30 = r.tp_30m or 0
        win_rate = wins / max(closed, 1)
        confidence = "HIGH" if closed >= 100 else ("MEDIUM" if closed >= 30 else "LOW")
        profiles.append({
            "profile_id": str(r.profile_id) if r.profile_id else None,
            "profile_name": r.profile_name,
            "source": r.source,
            "total_trades": r.total_trades,
            "closed_trades": closed,
            "wins": wins,
            "losses": r.losses or 0,
            "timeouts": r.timeouts or 0,
            "win_rate": round(win_rate, 4),
            "avg_pnl_pct": float(r.avg_pnl_pct or 0),
            "avg_mae_pct": float(r.avg_mae_pct or 0),
            "tp_30m_rate": round(tp30 / max(closed, 1), 4),
            "confidence_level": confidence,
        })

    return {"profiles": profiles, "lookback_days": lookback_days}


# ── 5 & 6. Top winners / losers ───────────────────────────────────────────────

@router.get("/indicators/top-winners")
async def get_top_winners(
    run_id: Optional[str] = None,
    min_cases: int = Query(default=10, ge=1),
    confidence_level: Optional[str] = None,
    indicator: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    return await _get_indicator_stats(db, user_id, "winning_indicator", run_id, min_cases, confidence_level, indicator, limit)


@router.get("/indicators/top-losers")
async def get_top_losers(
    run_id: Optional[str] = None,
    min_cases: int = Query(default=10, ge=1),
    confidence_level: Optional[str] = None,
    indicator: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    return await _get_indicator_stats(db, user_id, "losing_indicator", run_id, min_cases, confidence_level, indicator, limit)


async def _get_indicator_stats(db, user_id, role, run_id, min_cases, confidence_level, indicator, limit):
    selected_run_id: Optional[UUID] = None
    if run_id:
        try:
            selected_run_id = UUID(run_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid run_id")
    else:
        selected_run_id = await db.scalar(
            select(ProfileIntelligenceRun.id)
            .where(
                ProfileIntelligenceRun.user_id == user_id,
                ProfileIntelligenceRun.status.in_(("completed", "completed_with_errors")),
            )
            .order_by(ProfileIntelligenceRun.run_at.desc())
            .limit(1)
        )

    if selected_run_id is None:
        return {"indicators": [], "role": role, "run_id": None}

    q = select(ProfileIndicatorStats).where(
        ProfileIndicatorStats.user_id == user_id,
        ProfileIndicatorStats.run_id == selected_run_id,
        ProfileIndicatorStats.role_detected == role,
        ProfileIndicatorStats.total_cases >= min_cases,
    )
    if confidence_level:
        q = q.where(ProfileIndicatorStats.confidence_level == confidence_level)
    if indicator:
        q = q.where(ProfileIndicatorStats.indicator == indicator)
    q = q.order_by(ProfileIndicatorStats.lift_vs_base.desc().nullslast()).limit(limit)
    stats = (await db.execute(q)).scalars().all()
    return {
        "indicators": [_ind_to_dict(s) for s in stats],
        "role": role,
        "run_id": str(selected_run_id),
    }


# ── 7. List combinations ──────────────────────────────────────────────────────

@router.get("/combinations")
async def list_combinations(
    run_id: Optional[str] = None,
    confidence_level: Optional[str] = None,
    combination_type: Optional[str] = None,
    setup_family: Optional[str] = None,
    tested: Optional[bool] = None,
    overfit_risk: Optional[bool] = None,
    min_champion_score: Optional[float] = None,
    limit: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    q = select(ProfileRuleCombination).where(ProfileRuleCombination.user_id == user_id)
    if run_id:
        try: q = q.where(ProfileRuleCombination.run_id == UUID(run_id))
        except ValueError: pass
    if confidence_level:
        q = q.where(ProfileRuleCombination.confidence_level == confidence_level)
    if combination_type:
        q = q.where(ProfileRuleCombination.combination_type == combination_type)
    if setup_family:
        q = q.where(ProfileRuleCombination.setup_family == setup_family)
    if tested is not None:
        q = q.where(ProfileRuleCombination.is_tested_live_shadow == tested)
    if overfit_risk is not None:
        q = q.where(ProfileRuleCombination.overfit_risk == overfit_risk)
    if min_champion_score is not None:
        q = q.where(ProfileRuleCombination.champion_score >= min_champion_score)
    q = q.order_by(ProfileRuleCombination.champion_score.desc().nullslast()).limit(limit)
    combos = (await db.execute(q)).scalars().all()
    return {"combinations": [_combo_to_dict(c) for c in combos]}


# ── 8. Combination detail ─────────────────────────────────────────────────────

@router.get("/combinations/{combination_id}")
async def get_combination(
    combination_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        cid = UUID(combination_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid combination_id")
    result = await db.execute(
        select(ProfileRuleCombination).where(
            ProfileRuleCombination.id == cid,
            ProfileRuleCombination.user_id == user_id,
        )
    )
    combo = result.scalars().first()
    if not combo:
        raise HTTPException(status_code=404, detail="Combination not found")
    return _combo_to_dict(combo)


# ── 8b. Create suggestion from combination ────────────────────────────────────

@router.post("/combinations/{combination_id}/create-suggestion")
async def create_suggestion_from_combination(
    combination_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Generate a ProfileSuggestion from a ProfileRuleCombination.
    Idempotent: if a suggestion already exists for this combination, returns it.
    No profile is created — user must call /suggestions/{id}/create-profile separately.
    """
    try:
        cid = UUID(combination_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid combination_id")

    combo_result = await db.execute(
        select(ProfileRuleCombination).where(
            ProfileRuleCombination.id == cid,
            ProfileRuleCombination.user_id == user_id,
        )
    )
    combo = combo_result.scalars().first()
    if not combo:
        raise HTTPException(status_code=404, detail="Combination not found")

    # Idempotency: return existing suggestion if one already exists
    existing_sugg = (await db.execute(
        select(ProfileSuggestion).where(
            ProfileSuggestion.user_id == user_id,
            ProfileSuggestion.source_combination_id == cid,
        ).limit(1)
    )).scalars().first()
    if existing_sugg:
        await log_pi_event(
            db, user_id, "suggestion_from_combination_already_exists",
            run_id=combo.run_id,
            combination_id=cid,
            suggestion_id=existing_sugg.id,
            event_description=f"Suggestion already exists for combination {cid}",
        )
        return {"suggestion": _sugg_to_dict(existing_sugg), "created": False}

    # Build suggestion from combination data
    from ..services.profile_suggestion_service import STANDARD_BLOCK_RULES

    win_rate = float(combo.win_rate or 0)
    tp30m = float(combo.tp_30m_rate or 0)
    avg_pnl = float(combo.avg_pnl_pct or 0)
    avg_mae = float(combo.avg_mae_pct or 0)
    total_cases = int(combo.total_cases or 0)

    evidence = {
        "win_rate": round(win_rate, 4),
        "tp_30m_rate": round(tp30m, 4),
        "avg_pnl_pct": round(avg_pnl, 4),
        "avg_mae_pct": round(avg_mae, 4),
        "total_cases": total_cases,
        "wins": combo.wins or 0,
        "losses": combo.losses or 0,
        "champion_score": float(combo.champion_score or 0),
        "lift_vs_base": float(combo.lift_vs_base or 0),
        "combination_type": combo.combination_type,
        "source_combination_id": str(cid),
    }

    suggested_signals = {"conditions": []} if not combo.signals_json else combo.signals_json
    suggested_scoring = combo.scoring_rules_json
    suggested_blocks = combo.block_rules_json or STANDARD_BLOCK_RULES

    comb_name = combo.suggested_name or f"COMBO_{str(cid)[:8].upper()}"
    profile_name = f"PI_{comb_name}"[:120]
    family = combo.setup_family or "unknown"

    confidence_level = combo.confidence_level or "LOW"

    quant_explanation = (
        f"Combination {comb_name} (type={combo.combination_type}) evaluated on {total_cases} cases. "
        f"Win rate: {win_rate*100:.1f}% | Avg P&L: {avg_pnl:+.2f}% | TP 30m: {tp30m*100:.1f}% | "
        f"Lift vs base: {float(combo.lift_vs_base or 0):.2f}x. "
        f"Confidence: {confidence_level}. "
        f"Generated via 'Generate Suggestion' from Combinations tab."
    )

    sugg = ProfileSuggestion(
        user_id=user_id,
        run_id=combo.run_id,
        source_combination_id=cid,
        suggested_profile_name=profile_name,
        suggested_profile_description=f"Auto-generated from combination: {comb_name}",
        suggested_profile_family=family,
        suggested_config_json={"source": "combination", "combination_id": str(cid)},
        suggested_signals_json=suggested_signals,
        suggested_scoring_json=suggested_scoring,
        suggested_block_rules_json=suggested_blocks,
        evidence_summary_json=evidence,
        quantitative_explanation=quant_explanation,
        confidence_score=float(combo.champion_score or 0),
        confidence_level=confidence_level,
        status="pending_user_approval",
    )
    db.add(sugg)
    await db.flush()

    await log_pi_event(
        db, user_id, "suggestion_created_from_combination",
        run_id=combo.run_id,
        combination_id=cid,
        suggestion_id=sugg.id,
        event_description=f"Suggestion created from combination {comb_name}",
        payload_json={"combination_id": str(cid), "combination_name": comb_name},
    )

    await db.commit()
    return {"suggestion": _sugg_to_dict(sugg), "created": True}


# ── 9. List suggestions ───────────────────────────────────────────────────────

@router.get("/suggestions")
async def list_suggestions(
    run_id: Optional[str] = None,
    status: Optional[str] = None,
    confidence_level: Optional[str] = None,
    family: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    q = select(ProfileSuggestion).where(ProfileSuggestion.user_id == user_id)
    if run_id:
        try: q = q.where(ProfileSuggestion.run_id == UUID(run_id))
        except ValueError: pass
    if status:
        q = q.where(ProfileSuggestion.status == status)
    if confidence_level:
        q = q.where(ProfileSuggestion.confidence_level == confidence_level)
    if family:
        q = q.where(ProfileSuggestion.suggested_profile_family == family)
    q = q.order_by(ProfileSuggestion.confidence_score.desc().nullslast()).limit(limit)
    suggestions = (await db.execute(q)).scalars().all()
    return {"suggestions": [_sugg_to_dict(s) for s in suggestions]}


# ── 10. Suggestion detail ─────────────────────────────────────────────────────

@router.get("/suggestions/{suggestion_id}")
async def get_suggestion(
    suggestion_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        sid = UUID(suggestion_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid suggestion_id")
    result = await db.execute(
        select(ProfileSuggestion).where(
            ProfileSuggestion.id == sid,
            ProfileSuggestion.user_id == user_id,
        )
    )
    s = result.scalars().first()
    if not s:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return _sugg_to_dict(s)


# ── 11. Create profile from suggestion ───────────────────────────────────────

@router.post("/suggestions/{suggestion_id}/create-profile")
async def create_profile_from_suggestion(
    suggestion_id: str,
    payload: CreateProfileRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Transform a suggestion into a new Strategy Profile.

    Security guarantees:
    - Profile always created with is_shadow_only=True, live_trading_enabled=False.
    - Only SHADOW_ONLY and DRAFT modes accepted.
    - Low confidence and overfit risk require explicit confirmation.
    - Full audit trail regardless of outcome.
    - Idempotent: same suggestion returns same profile.
    - dry_run=True previews without writing.
    """
    try:
        sid = UUID(suggestion_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid suggestion_id")

    if payload.mode not in ("SHADOW_ONLY", "DRAFT"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Mode '{payload.mode}' não é suportado. "
                "Live trading permanece desativado. Use SHADOW_ONLY ou DRAFT."
            ),
        )

    from ..services.profile_create_service import ProfileCreateService
    svc = ProfileCreateService()

    try:
        result = await svc.create_from_suggestion(
            db=db,
            user_id=user_id,
            suggestion_id=sid,
            profile_name=payload.profile_name,
            profile_description=payload.profile_description,
            mode=payload.mode,
            confirm_low_confidence=payload.confirm_low_confidence,
            confirm_overfit_risk=payload.confirm_overfit_risk,
            create_missing_master_rules=payload.create_missing_master_rules,
            reuse_existing_master_rules=payload.reuse_existing_master_rules,
            assign_to_watchlist_id=payload.assign_to_watchlist_id,
            dry_run=payload.dry_run,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("[PI API] create-profile failed for suggestion %s: %s", suggestion_id, exc)
        raise HTTPException(status_code=500, detail=f"Erro interno ao criar profile: {exc!s:.200}")

    status = result.get("status")
    if status == "blocked":
        raise HTTPException(status_code=409, detail=result)

    return result


# ── 11b. Generate AI explanation ──────────────────────────────────────────────

@router.post("/suggestions/{suggestion_id}/explain")
async def explain_suggestion(
    suggestion_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        sid = UUID(suggestion_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid suggestion_id")
    from ..services.profile_ai_explanation_service import ProfileAIExplanationService
    svc = ProfileAIExplanationService()
    explanation = await svc.explain_suggestion(db=db, user_id=user_id, suggestion_id=sid)
    await db.commit()
    return {"suggestion_id": suggestion_id, "explanation": explanation}


# ── 12. Audit log ─────────────────────────────────────────────────────────────

@router.get("/audit")
async def get_audit_log(
    run_id: Optional[str] = None,
    suggestion_id: Optional[str] = None,
    combination_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    q = select(ProfileIntelligenceAuditLog).where(
        ProfileIntelligenceAuditLog.user_id == user_id
    )
    if run_id:
        try: q = q.where(ProfileIntelligenceAuditLog.run_id == UUID(run_id))
        except ValueError: pass
    if suggestion_id:
        try: q = q.where(ProfileIntelligenceAuditLog.suggestion_id == UUID(suggestion_id))
        except ValueError: pass
    if combination_id:
        try: q = q.where(ProfileIntelligenceAuditLog.combination_id == UUID(combination_id))
        except ValueError: pass
    if event_type:
        q = q.where(ProfileIntelligenceAuditLog.event_type == event_type)
    q = q.order_by(ProfileIntelligenceAuditLog.created_at.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return {"events": [_audit_to_dict(r) for r in rows]}


# ── 13 & 14. Settings ─────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..services.config_service import config_service
    try:
        cfg = await config_service.get_config(db, "profile_intelligence", user_id)
        settings = {**_DEFAULT_SETTINGS, **(cfg or {})}
    except Exception:
        settings = _DEFAULT_SETTINGS.copy()
    return {"settings": settings}


@router.put("/settings")
async def update_settings(
    payload: PISettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..services.config_service import config_service
    try:
        current = await config_service.get_config(db, "profile_intelligence", user_id) or {}
    except Exception:
        current = {}

    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    new_settings = {**_DEFAULT_SETTINGS, **current, **updates}

    try:
        await config_service.update_config(
            db=db,
            config_type="profile_intelligence",
            user_id=user_id,
            new_json=new_settings,
            changed_by=user_id,
            change_description="Profile Intelligence settings update",
        )
    except Exception as exc:
        logger.warning("[PI API] config_service.update_config failed (%s), using direct upsert", exc)
        await db.execute(text("""
            INSERT INTO config_profiles (user_id, config_type, config_json, is_active, created_at, updated_at)
            VALUES (:uid, 'profile_intelligence', :cfg::jsonb, true, NOW(), NOW())
            ON CONFLICT DO NOTHING
        """), {"uid": str(user_id), "cfg": json.dumps(new_settings)})
        await db.execute(text("""
            UPDATE config_profiles SET config_json = :cfg::jsonb, updated_at = NOW()
            WHERE user_id = :uid AND config_type = 'profile_intelligence' AND is_active = true
        """), {"uid": str(user_id), "cfg": json.dumps(new_settings)})
        await db.commit()

    return {"settings": new_settings}


# Profile Intelligence Auto-Pilot

async def _queue_autopilot_cycle(
    background_tasks: BackgroundTasks,
    user_id: UUID,
) -> Dict[str, Any]:
    try:
        from ..tasks.profile_intelligence_job import run_for_user
        task = run_for_user.delay(str(user_id), False)
        return {"cycle_status": "queued", "task_id": task.id}
    except Exception as exc:
        logger.warning("[PI API] Auto-Pilot queue dispatch failed, using background task: %s", exc)
        from ..database import AsyncSessionLocal
        from ..services.profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService

        async def _background():
            async with AsyncSessionLocal() as bg_db:
                await ProfileIntelligenceAutopilotService().run_cycle(bg_db, user_id)

        background_tasks.add_task(_background)
        return {"cycle_status": "queued", "task_id": None}


@router.get("/autopilot")
async def get_autopilot_status(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..models.profile_intelligence_autopilot import (
        ProfileIntelligenceAutopilotCycle,
        ProfileIntelligenceAutopilotReport,
    )
    from ..services.profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService

    service = ProfileIntelligenceAutopilotService()
    row, settings = await service.get_settings(db, user_id)
    latest_cycle = await db.scalar(
        select(ProfileIntelligenceAutopilotCycle).where(
            ProfileIntelligenceAutopilotCycle.user_id == user_id
        ).order_by(ProfileIntelligenceAutopilotCycle.started_at.desc()).limit(1)
    )
    latest_report = await db.scalar(
        select(ProfileIntelligenceAutopilotReport).where(
            ProfileIntelligenceAutopilotReport.user_id == user_id
        ).order_by(ProfileIntelligenceAutopilotReport.created_at.desc()).limit(1)
    )
    state_rows = (await db.execute(text("""
        SELECT state, COUNT(*)::int AS total
        FROM profile_intelligence_autopilot_candidates
        WHERE user_id = :uid
        GROUP BY state
    """), {"uid": str(user_id)})).fetchall()
    return {
        "enabled": row.enabled,
        "settings": settings,
        "enabled_at": row.enabled_at.isoformat() if row.enabled_at else None,
        "disabled_at": row.disabled_at.isoformat() if row.disabled_at else None,
        "last_cycle_at": row.last_cycle_at.isoformat() if row.last_cycle_at else None,
        "latest_cycle": {
            "id": str(latest_cycle.id),
            "status": latest_cycle.status,
            "checkpoint": latest_cycle.checkpoint,
            "window_start": latest_cycle.window_start.isoformat(),
            "started_at": latest_cycle.started_at.isoformat(),
            "completed_at": latest_cycle.completed_at.isoformat() if latest_cycle.completed_at else None,
            "metrics": latest_cycle.metrics_json or {},
            "errors": latest_cycle.errors_json or [],
        } if latest_cycle else None,
        "candidate_counts": {item.state: item.total for item in state_rows},
        "latest_report": latest_report.report_json if latest_report else None,
    }


@router.put("/autopilot")
async def update_autopilot_status(
    payload: AutopilotSettingsUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..services.profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService

    service = ProfileIntelligenceAutopilotService()
    current, _ = await service.get_settings(db, user_id)
    was_enabled = current.enabled
    result = await service.set_enabled(
        db, user_id, payload.enabled, payload.settings
    )
    if payload.enabled and not was_enabled:
        result.update(await _queue_autopilot_cycle(background_tasks, user_id))
    return result


@router.post("/autopilot/run")
async def trigger_autopilot_cycle(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..services.profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService
    row, _ = await ProfileIntelligenceAutopilotService().get_settings(db, user_id)
    if not row.enabled:
        raise HTTPException(status_code=409, detail="Auto-Pilot global está desligado")
    await db.commit()
    queued = await _queue_autopilot_cycle(background_tasks, user_id)
    return {"status": queued["cycle_status"], "task_id": queued["task_id"]}


@router.get("/autopilot/candidates")
async def list_autopilot_candidates(
    state: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..models.profile import Profile
    from ..models.pipeline_watchlist import PipelineWatchlist
    from ..models.profile_intelligence_autopilot import ProfileIntelligenceAutopilotCandidate

    query = (
        select(ProfileIntelligenceAutopilotCandidate, Profile.name, PipelineWatchlist.name)
        .join(Profile, Profile.id == ProfileIntelligenceAutopilotCandidate.profile_id)
        .outerjoin(PipelineWatchlist, PipelineWatchlist.id == ProfileIntelligenceAutopilotCandidate.shadow_watchlist_id)
        .where(ProfileIntelligenceAutopilotCandidate.user_id == user_id)
    )
    if state:
        query = query.where(ProfileIntelligenceAutopilotCandidate.state == state)
    rows = (await db.execute(
        query.order_by(ProfileIntelligenceAutopilotCandidate.updated_at.desc()).limit(limit)
    )).all()
    return {"candidates": [{
        "id": str(candidate.id),
        "profile_id": str(candidate.profile_id),
        "profile_name": profile_name,
        "origin_profile_id": str(candidate.origin_profile_id) if candidate.origin_profile_id else None,
        "previous_profile_id": str(candidate.previous_profile_id) if candidate.previous_profile_id else None,
        "watchlist_id": str(candidate.shadow_watchlist_id) if candidate.shadow_watchlist_id else None,
        "watchlist_name": watchlist_name,
        "target_watchlist_id": str(candidate.target_watchlist_id) if candidate.target_watchlist_id else None,
        "state": candidate.state,
        "version_number": candidate.version_number,
        "observed_trades": candidate.observed_trades,
        "observed_win_rate": float(candidate.observed_win_rate) if candidate.observed_win_rate is not None else None,
        "observed_avg_pnl_pct": float(candidate.observed_avg_pnl_pct) if candidate.observed_avg_pnl_pct is not None else None,
        "promotion_win_rate": float(candidate.promotion_win_rate) if candidate.promotion_win_rate is not None else None,
        "promotion_avg_pnl_pct": float(candidate.promotion_avg_pnl_pct) if candidate.promotion_avg_pnl_pct is not None else None,
        "reason": candidate.decision_reason,
        "evidence": candidate.evidence_json or {},
        "created_at": candidate.created_at.isoformat(),
        "updated_at": candidate.updated_at.isoformat(),
    } for candidate, profile_name, watchlist_name in rows]}


@router.get("/autopilot/reports")
async def list_autopilot_reports(
    limit: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..models.profile_intelligence_autopilot import ProfileIntelligenceAutopilotReport
    rows = (await db.execute(
        select(ProfileIntelligenceAutopilotReport).where(
            ProfileIntelligenceAutopilotReport.user_id == user_id
        ).order_by(ProfileIntelligenceAutopilotReport.created_at.desc()).limit(limit)
    )).scalars().all()
    return {"reports": [{
        "id": str(row.id),
        "cycle_id": str(row.cycle_id),
        "report": row.report_json,
        "created_at": row.created_at.isoformat(),
    } for row in rows]}


@router.get("/autopilot/audit")
async def list_autopilot_audit(
    event_type: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..models.profile_intelligence_autopilot import ProfileIntelligenceAutopilotAudit
    query = select(ProfileIntelligenceAutopilotAudit).where(
        ProfileIntelligenceAutopilotAudit.user_id == user_id
    )
    if event_type:
        query = query.where(ProfileIntelligenceAutopilotAudit.event_type == event_type)
    rows = (await db.execute(
        query.order_by(ProfileIntelligenceAutopilotAudit.created_at.desc()).limit(limit)
    )).scalars().all()
    return {"events": [{
        "id": str(row.id),
        "event_type": row.event_type,
        "cycle_id": str(row.cycle_id) if row.cycle_id else None,
        "candidate_id": str(row.candidate_id) if row.candidate_id else None,
        "profile_id": str(row.profile_id) if row.profile_id else None,
        "watchlist_id": str(row.watchlist_id) if row.watchlist_id else None,
        "combination_id": str(row.combination_id) if row.combination_id else None,
        "suggestion_id": str(row.suggestion_id) if row.suggestion_id else None,
        "input_metrics": row.input_metrics_json or {},
        "thresholds": row.thresholds_json or {},
        "decision": row.decision,
        "reason": row.reason,
        "result": row.result_json or {},
        "created_at": row.created_at.isoformat(),
    } for row in rows]}


# ── Serialization helpers ─────────────────────────────────────────────────────

def _run_to_dict(r: ProfileIntelligenceRun) -> dict:
    return {
        "id": str(r.id), "user_id": str(r.user_id),
        "run_at": r.run_at.isoformat() if r.run_at else None,
        "lookback_days": r.lookback_days, "min_closed_trades": r.min_closed_trades,
        "status": r.status, "engine_version": r.engine_version,
        "total_profiles": r.total_profiles, "total_shadow_trades": r.total_shadow_trades,
        "total_closed_trades": r.total_closed_trades,
        "total_opportunity_snapshots": r.total_opportunity_snapshots or 0,
        "base_win_rate": float(r.base_win_rate or 0),
        "base_avg_pnl_pct": float(r.base_avg_pnl_pct or 0),
        "base_tp_30m_rate": float(r.base_tp_30m_rate or 0),
        "error_message": r.error_message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _ind_to_dict(s: ProfileIndicatorStats) -> dict:
    return {
        "id": str(s.id), "indicator": s.indicator, "bucket_label": s.bucket_label,
        "total_cases": s.total_cases, "wins": s.wins, "losses": s.losses,
        "win_rate": float(s.win_rate or 0),
        "loss_rate": float(s.loss_rate or 0) if s.loss_rate is not None else None,
        "avg_pnl_pct": float(s.avg_pnl_pct or 0),
        "avg_mae_pct": float(s.avg_mae_pct or 0) if s.avg_mae_pct is not None else None,
        "avg_mfe_pct": float(s.avg_mfe_pct or 0) if s.avg_mfe_pct is not None else None,
        "avg_holding_seconds": float(s.avg_holding_seconds or 0) if s.avg_holding_seconds is not None else None,
        "tp_30m_rate": float(s.tp_30m_rate or 0) if s.tp_30m_rate is not None else None,
        "lift_vs_base": float(s.lift_vs_base or 0),
        "winner_presence_pct": float(s.winner_presence_pct or 0),
        "loser_presence_pct": float(s.loser_presence_pct or 0),
        "confidence_score": float(s.confidence_score or 0),
        "confidence_level": s.confidence_level, "role_detected": s.role_detected,
        "source_profiles": s.source_profiles,
        "evidence_json": s.evidence_json,
    }


def _combo_to_dict(c: ProfileRuleCombination) -> dict:
    return {
        "id": str(c.id), "run_id": str(c.run_id),
        "combination_hash": c.combination_hash,
        "combination_type": c.combination_type, "setup_family": c.setup_family,
        "suggested_name": c.suggested_name, "rules_json": c.rules_json,
        "signals_json": c.signals_json,
        "block_rules_json": c.block_rules_json,
        "source_profiles": c.source_profiles,
        "total_cases": c.total_cases, "wins": c.wins, "losses": c.losses,
        "win_rate": float(c.win_rate or 0),
        "avg_pnl_pct": float(c.avg_pnl_pct or 0),
        "avg_mae_pct": float(c.avg_mae_pct or 0) if c.avg_mae_pct is not None else None,
        "avg_mfe_pct": float(c.avg_mfe_pct or 0) if c.avg_mfe_pct is not None else None,
        "tp_30m_rate": float(c.tp_30m_rate or 0),
        "lift_vs_base": float(c.lift_vs_base or 0),
        "champion_score": float(c.champion_score or 0),
        "confidence_level": c.confidence_level,
        "overfit_risk": c.overfit_risk,
        "is_tested_live_shadow": c.is_tested_live_shadow,
        "degradation_pct": float(c.degradation_pct or 0) if c.degradation_pct else None,
        "discovery_metrics_json": c.discovery_metrics_json,
        "validation_metrics_json": c.validation_metrics_json,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _sugg_to_dict(s: ProfileSuggestion) -> dict:
    return {
        "id": str(s.id), "run_id": str(s.run_id),
        "source_combination_id": str(s.source_combination_id) if s.source_combination_id else None,
        "suggested_profile_name": s.suggested_profile_name,
        "suggested_profile_description": s.suggested_profile_description,
        "suggested_profile_family": s.suggested_profile_family,
        "suggested_config_json": s.suggested_config_json,
        "suggested_signals_json": s.suggested_signals_json,
        "suggested_block_rules_json": s.suggested_block_rules_json,
        "evidence_summary_json": s.evidence_summary_json,
        "quantitative_explanation": s.quantitative_explanation,
        "ai_explanation": s.ai_explanation,
        "risk_notes": s.risk_notes,
        "confidence_score": float(s.confidence_score or 0),
        "confidence_level": s.confidence_level, "status": s.status,
        "created_profile_id": str(s.created_profile_id) if s.created_profile_id else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _audit_to_dict(r: ProfileIntelligenceAuditLog) -> dict:
    return {
        "id": str(r.id), "event_type": r.event_type,
        "event_description": r.event_description,
        "run_id": str(r.run_id) if r.run_id else None,
        "suggestion_id": str(r.suggestion_id) if r.suggestion_id else None,
        "combination_id": str(r.combination_id) if r.combination_id else None,
        "model_provider": r.model_provider, "model_name": r.model_name,
        "payload_json": r.payload_json,
        "result_json": r.result_json,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
