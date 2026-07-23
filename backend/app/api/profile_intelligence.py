"""Profile Intelligence Engine API."""
from __future__ import annotations

from importlib.util import find_spec
import json
import logging
import os
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.profile import Profile
from ..models.profile_score_optimization import ProfileScoreOptimizationRun
from ..models.profile_intelligence import (
    ProfileIntelligenceRun,
    ProfileIndicatorStats,
    ProfileRuleCombination,
    ProfileSuggestion,
    ProfileIntelligenceAuditLog,
    MLModelRegistry,
    ProductionChampionControl,
    AlgorithmForwardValidation,
    AutopilotAutonomyPolicy,
)
from .config import get_current_user_id
from ..core.mutation_types import BLOCKED_REASON_TO_DECISION, MutationStatus
from ..services.metric_contracts import build_metric_contract
from ..schemas.profile_intelligence import (
    RunRequest,
    RunResponse,
    PISettingsUpdate,
    CreateProfileRequest,
    AutopilotSettingsUpdate,
    CandidateApprovalRequest,
    CandidateRejectionRequest,
    IndicatorShadowAdjustmentRequest,
    ManualAdjustmentCreateRequest,
    ManualAdjustmentUpdateRequest,
    ManualAdjustmentApprovalRequest,
    ManualAdjustmentRollbackRequest,
    ScoreThresholdSimulationRequest,
    ScoreGlobalAnalysisRequest,
    ProfileIntelligenceAIModelRequest,
)
from ..services.profile_intelligence_audit_service import log_pi_event
from ..services.profile_intelligence_manual_service import (
    profile_intelligence_manual_service,
    public_manual_adjustment,
)
from ..services.profile_intelligence_contract import (
    DATASET_VERSION,
    LABEL_VERSION,
    official_params,
    official_where,
)
from ..services.profile_score_intelligence_service import (
    profile_score_intelligence_service,
)
from ..services.profile_score_optimization_service import (
    profile_score_optimization_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile-intelligence", tags=["profile-intelligence"])


_MANUAL_CONFLICTS = {
    "base_version_changed", "preview_hash_mismatch", "stale_profile_version",
    "rollback_current_version_changed", "profile_and_champion_config_mismatch",
}


def _manual_apply_enabled() -> bool:
    """Fail closed: only the literal ``true`` enables the mutation boundary."""
    return os.getenv("PI_MANUAL_APPLY_ENABLED", "false").strip().lower() == "true"


def _manual_http_error(exc: ValueError) -> HTTPException:
    code = str(exc)
    return HTTPException(status_code=409 if code in _MANUAL_CONFLICTS else 422, detail=code)

_DEFAULT_SETTINGS = {
    "ai_provider": "anthropic",
    "ai_model": "claude-haiku-4-5-20251001",
    "ai_model_status": "NOT_TESTED",
    "analysis_skill_version": "profile_intelligence_analysis_skill_v2",
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
    "analysis_sources": ["L1_SPECTRUM", "L3", "L3_LAB"],
    "indicator_winning_lift": 1.15,
    "indicator_losing_winrate_ratio": 0.85,
    "validation_min_discovery_trades": 30,
    "validation_min_trades": 20,
    "validation_min_lift": 1.15,
    "validation_min_winrate_delta": 0.05,
    "validation_max_single_symbol_share": 0.40,
    "validation_max_single_day_share": 0.40,
    "validation_min_distinct_symbols": 3,
    "validation_min_distinct_days": 3,
    "validation_min_assoc_support": 0.02,
    "validation_min_assoc_confidence": 0.55,
    "validation_min_lift_retention": 0.70,
    "adjustment_min_profile_trades": 50,
    "adjustment_max_win_rate": 0.35,
    "adjustment_score_bump": 5,
    "adjustment_score_cap": 85,
    "score_global_rapid_sl_candles": 12,
    "score_global_max_analysis_rows": 100000,
    "score_global_min_bucket_trades": 30,
    "score_global_penalty_points": -5,
    "score_global_max_changes_per_profile": 3,
    "score_global_ai_timeout_seconds": 180,
    "score_global_replay_min_retention": 0.70,
    "score_global_replay_max_tp_loss_rate": 0.05,
    "score_global_replay_min_sl_reduction_rate": 0.02,
    "score_global_challenger_min_days": 7,
    "score_global_challenger_min_closed": 100,
    "score_global_challenger_min_tp": 20,
    "score_global_challenger_min_sl": 20,
    "score_global_challenger_min_distinct_symbols": 3,
    "score_global_challenger_min_distinct_days": 3,
    "score_global_challenger_max_single_symbol_share": 0.40,
    "score_global_challenger_max_single_day_share": 0.40,
}

_ML_CHALLENGER_FLAGS = {
    "enable_lightgbm": "lightgbm",
    "enable_catboost": "catboost",
}


def _normalize_unimplemented_ml_flags(
    settings: Optional[Dict[str, Any]],
) -> tuple[Dict[str, Any], list[str]]:
    normalized = dict(settings or {})
    warnings = []
    for flag, package in _ML_CHALLENGER_FLAGS.items():
        if normalized.get(flag) is True and find_spec(package) is None:
            warnings.append(f"{package} não está instalado neste ambiente — flag ignorado")
            normalized[flag] = False
    return normalized, warnings


def _ml_challenger_status() -> Dict[str, Dict[str, Any]]:
    try:
        from ..services.ml_challenger_service import get_challenger_status
        return get_challenger_status()
    except Exception:
        status = {
            "available": False,
            "implemented": True,
            "installed": False,
            "operational": False,
            "status": "import_error",
            "effective_contribution": 0,
            "can_train": False,
            "can_infer": False,
            "can_generate_suggestions": False,
            "influences_autopilot": False,
        }
        return {"lightgbm": dict(status), "catboost": dict(status)}


# ── Manual, versioned adjustments ────────────────────────────────────────────

@router.get("/manual-adjustments/capabilities")
async def manual_adjustment_capabilities(
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    enabled = _manual_apply_enabled()
    return {
        "manual_read_enabled": True,
        "manual_draft_enabled": True,
        "manual_preview_enabled": True,
        "manual_apply_enabled": enabled,
        "manual_apply_reason": None if enabled else "PI_MANUAL_APPLY_ENABLED=false",
        "autopilot_enabled": False,
        "live_activation_enabled": False,
        "blocking_reasons": [] if enabled else ["PI_MANUAL_APPLY_ENABLED=false"],
        "reject_enabled": True,
        # Rollback remains independent as the emergency exit for any
        # adjustment applied before a later feature-flag change.
        "rollback_enabled": True,
    }


@router.get("/manual-adjustments/eligible-profiles")
async def list_manual_adjustment_profiles(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Existing operator-owned L3 profiles only; never generated/shadow profiles."""
    return {"items": await profile_intelligence_manual_service.eligible_profiles(db, user_id)}


@router.post("/manual-adjustments", status_code=201)
async def create_manual_adjustment(
    request: ManualAdjustmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        result = await profile_intelligence_manual_service.create(db, user_id, request.model_dump())
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise _manual_http_error(exc) from exc


@router.get("/manual-adjustments")
async def list_manual_adjustments(
    state: Optional[str] = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    items = await profile_intelligence_manual_service.list(db, user_id, state, limit)
    return {"items": items, "total": len(items)}


@router.get("/manual-adjustments/{adjustment_id}")
async def get_manual_adjustment(
    adjustment_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        row = await profile_intelligence_manual_service.get(db, user_id, adjustment_id)
        return public_manual_adjustment(row)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/manual-adjustments/{adjustment_id}")
async def update_manual_adjustment(
    adjustment_id: UUID,
    request: ManualAdjustmentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        result = await profile_intelligence_manual_service.update(
            db, user_id, adjustment_id, request.model_dump(exclude_unset=True),
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise _manual_http_error(exc) from exc


@router.post("/manual-adjustments/{adjustment_id}/preview")
async def preview_manual_adjustment(
    adjustment_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        result = await profile_intelligence_manual_service.preview(db, user_id, adjustment_id)
        await db.commit()
        return result
    except ValueError as exc:
        # Preserve an explicit CONFLICTED state/event, if the service set one.
        if str(exc) in _MANUAL_CONFLICTS:
            await db.commit()
        else:
            await db.rollback()
        raise _manual_http_error(exc) from exc


@router.post("/manual-adjustments/{adjustment_id}/approve-and-apply")
async def approve_and_apply_manual_adjustment(
    adjustment_id: UUID,
    request: ManualAdjustmentApprovalRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    if not _manual_apply_enabled():
        raise HTTPException(
            status_code=503,
            detail="manual_apply_disabled: PI_MANUAL_APPLY_ENABLED=false",
        )
    try:
        result = await profile_intelligence_manual_service.approve_and_apply(
            db, user_id, adjustment_id, preview_hash=request.preview_hash,
            justification=request.justification, confirm_risk=request.confirm_risk,
        )
        await db.commit()
        return result
    except ValueError as exc:
        if str(exc) in _MANUAL_CONFLICTS:
            await db.commit()
        else:
            await db.rollback()
        raise _manual_http_error(exc) from exc


@router.post("/manual-adjustments/{adjustment_id}/reject")
async def reject_manual_adjustment(
    adjustment_id: UUID,
    request: ManualAdjustmentRollbackRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        result = await profile_intelligence_manual_service.reject(db, user_id, adjustment_id, request.reason)
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise _manual_http_error(exc) from exc


@router.post("/manual-adjustments/{adjustment_id}/rollback")
async def rollback_manual_adjustment(
    adjustment_id: UUID,
    request: ManualAdjustmentRollbackRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        result = await profile_intelligence_manual_service.rollback(db, user_id, adjustment_id, request.reason)
        await db.commit()
        return result
    except ValueError as exc:
        if str(exc) in _MANUAL_CONFLICTS:
            await db.commit()
        else:
            await db.rollback()
        raise _manual_http_error(exc) from exc


# ── 1. Overview ──────────────────────────────────────────────────────────────

# Score Intelligence: read-only, point-in-time analytics.
async def _score_intelligence_analysis(
    *, db: AsyncSession, user_id: UUID, lookback_days: int,
    source: str | None, profile_id: UUID | None,
    profile_version_id: UUID | None, score_engine_version_id: UUID | None,
    timeframe: str | None,
) -> Dict[str, Any]:
    try:
        return await profile_score_intelligence_service.analyze(
            db, user_id=user_id, lookback_days=lookback_days, source=source,
            profile_id=profile_id, profile_version_id=profile_version_id,
            score_engine_version_id=score_engine_version_id, timeframe=timeframe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/score-intelligence/overview")
async def get_score_intelligence_overview(
    lookback_days: int = Query(default=30, ge=7, le=365),
    source: str | None = Query(default=None),
    profile_id: UUID | None = Query(default=None),
    profile_version_id: UUID | None = Query(default=None),
    score_engine_version_id: UUID | None = Query(default=None),
    timeframe: str | None = Query(default=None, max_length=16),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    return await _score_intelligence_analysis(
        db=db, user_id=user_id, lookback_days=lookback_days, source=source,
        profile_id=profile_id, profile_version_id=profile_version_id,
        score_engine_version_id=score_engine_version_id, timeframe=timeframe,
    )


@router.get("/score-intelligence/threshold-analysis")
async def get_score_threshold_analysis(
    lookback_days: int = Query(default=30, ge=7, le=365),
    source: str | None = Query(default=None),
    profile_id: UUID | None = Query(default=None),
    profile_version_id: UUID | None = Query(default=None),
    score_engine_version_id: UUID | None = Query(default=None),
    timeframe: str | None = Query(default=None, max_length=16),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    analysis = await _score_intelligence_analysis(
        db=db, user_id=user_id, lookback_days=lookback_days, source=source,
        profile_id=profile_id, profile_version_id=profile_version_id,
        score_engine_version_id=score_engine_version_id, timeframe=timeframe,
    )
    return {
        "status": analysis.get("status"), "read_only": True,
        "scope": analysis.get("scope"), "current_thresholds": analysis.get("current_thresholds", []),
        "recommendation": analysis.get("recommendation"), "association_not_causation": True,
    }


@router.get("/score-intelligence/distribution")
async def get_score_distribution(
    score: str = Query(...), bucket_mode: str = Query(default="fixed"),
    lookback_days: int = Query(default=30, ge=7, le=365),
    source: str | None = Query(default=None), profile_id: UUID | None = Query(default=None),
    profile_version_id: UUID | None = Query(default=None),
    score_engine_version_id: UUID | None = Query(default=None),
    timeframe: str | None = Query(default=None, max_length=16),
    db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        return await profile_score_intelligence_service.get_distribution(
            db, user_id=user_id, score=score, bucket_mode=bucket_mode,
            lookback_days=lookback_days, source=source, profile_id=profile_id,
            profile_version_id=profile_version_id,
            score_engine_version_id=score_engine_version_id, timeframe=timeframe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/score-intelligence/version-comparison")
async def get_score_version_comparison(
    lookback_days: int = Query(default=90, ge=7, le=365),
    source: str | None = Query(default=None), profile_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        return await profile_score_intelligence_service.version_comparison(
            db, user_id=user_id, lookback_days=lookback_days, source=source, profile_id=profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/score-intelligence/simulate-threshold")
async def simulate_score_threshold(
    request: ScoreThresholdSimulationRequest,
    db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        return await profile_score_intelligence_service.simulate(
            db, user_id=user_id, score=request.score, threshold=request.threshold,
            lookback_days=request.lookback_days, source=request.source,
            profile_id=request.profile_id, profile_version_id=request.profile_version_id,
            score_engine_version_id=request.score_engine_version_id, timeframe=request.timeframe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/score-intelligence/global-overview")
async def get_score_global_overview(
    lookback_days: int = Query(default=30, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        return await profile_score_optimization_service.overview(
            db, user_id, lookback_days
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/score-intelligence/ai-models")
async def get_profile_intelligence_ai_models(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    return await profile_score_optimization_service.ai_models(db, user_id)


@router.post("/score-intelligence/ai-models/refresh")
async def refresh_profile_intelligence_ai_models(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        return await profile_score_optimization_service.refresh_ai_models(db, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/score-intelligence/ai-models/test")
async def test_profile_intelligence_ai_model(
    request: ProfileIntelligenceAIModelRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        return await profile_score_optimization_service.test_ai_model(
            db, user_id, request.model_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/score-intelligence/ai-model")
async def save_profile_intelligence_ai_model(
    request: ProfileIntelligenceAIModelRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        result = await profile_score_optimization_service.save_ai_model(
            db, user_id, request.model_id, request.reason
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/score-intelligence/global-analysis", status_code=202)
async def run_score_global_analysis(
    request: ScoreGlobalAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        result, created = await profile_score_optimization_service.queue_global_analysis(
            db, user_id, request.lookback_days, request.idempotency_key
        )
        await db.commit()
        if created:
            try:
                from ..tasks.profile_score_optimization import analyze

                task = analyze.delay(result["id"])
                result["task_id"] = task.id
                result["queue"] = "structural_compute"
                logger.info(
                    "[PI-Score] analysis queued run=%s task=%s user=%s",
                    result["id"], task.id, user_id,
                )
            except Exception as dispatch_exc:
                await db.execute(
                    update(ProfileScoreOptimizationRun)
                    .where(ProfileScoreOptimizationRun.id == UUID(result["id"]))
                    .values(
                        status="AI_FAILED",
                        error_code=f"dispatch_failed:{dispatch_exc}"[:120],
                    )
                )
                await db.commit()
                raise HTTPException(
                    status_code=503,
                    detail="profile_score_analysis_dispatch_failed",
                ) from dispatch_exc
        return result
    except (ValueError, RuntimeError) as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/score-intelligence/optimization-runs")
async def list_score_optimization_runs(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    items = await profile_score_optimization_service.list_runs(db, user_id, limit)
    return {"items": items, "total": len(items)}


@router.get("/score-intelligence/optimization-runs/{run_id}")
async def get_score_optimization_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        return await profile_score_optimization_service.get_run(db, user_id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/score-intelligence/optimization-runs/{run_id}/download")
async def download_score_optimization_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Response:
    try:
        payload = await profile_score_optimization_service.get_run(
            db, user_id, run_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    envelope = payload.get("adjustment_envelope") or {}
    return Response(
        content=json.dumps(envelope, ensure_ascii=False, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="profile-score-adjustments-{run_id}.json"'
            )
        },
    )


@router.post("/score-intelligence/optimization-runs/{run_id}/replay")
async def replay_score_optimization_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        result = await profile_score_optimization_service.replay(db, user_id, run_id)
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/score-intelligence/optimization-runs/{run_id}/challengers")
async def create_score_optimization_challengers(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        result = await profile_score_optimization_service.create_challengers(
            db, user_id, run_id
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/score-intelligence/performance-evolution")
async def get_score_performance_evolution(
    profile_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    return await profile_score_optimization_service.performance(
        db, user_id, profile_id
    )


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

    manual_counts = (await db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE state IN ('MANUAL_DRAFT','PENDING_MANUAL_APPROVAL')) AS pending,
               COUNT(*) FILTER (WHERE state = 'APPLIED') AS applied,
               COUNT(*) FILTER (WHERE runtime_status = 'RUNTIME_REFRESH_PENDING') AS runtime_pending,
               COUNT(*) FILTER (WHERE runtime_status = 'RUNTIME_CONFIRMED') AS runtime_confirmed,
               COUNT(*) FILTER (WHERE state = 'ROLLED_BACK') AS rolled_back
          FROM profile_intelligence_manual_adjustments
         WHERE user_id = :uid
    """), {"uid": uid_str})).mappings().one()

    # Pending suggestions count
    pending_count = (await db.execute(text("""
        SELECT COUNT(*) FROM profile_suggestions
        WHERE user_id = :uid AND run_id = CAST(:run_id AS uuid)
          AND status = 'pending_user_approval'
    """), {"uid": uid_str, "run_id": str(last_run.id) if last_run else None})).scalar() or 0

    # High-confidence suggestions
    high_conf_count = (await db.execute(text("""
        SELECT COUNT(*) FROM profile_suggestions
        WHERE user_id = :uid AND run_id = CAST(:run_id AS uuid)
          AND confidence_level = 'HIGH'
          AND status NOT IN ('rejected', 'archived')
    """), {"uid": uid_str, "run_id": str(last_run.id) if last_run else None})).scalar() or 0

    # Total combinations (all-time)
    total_combos_count = (await db.execute(text("""
        SELECT COUNT(*) FROM profile_rule_combinations
        WHERE user_id = :uid AND run_id = CAST(:run_id AS uuid)
    """), {"uid": uid_str, "run_id": str(last_run.id) if last_run else None})).scalar() or 0

    # Combinations count (not yet shadow-tested)
    untested_count = (await db.execute(text("""
        SELECT COUNT(*) FROM profile_rule_combinations
        WHERE user_id = :uid AND run_id = CAST(:run_id AS uuid)
          AND is_tested_live_shadow = false
    """), {"uid": uid_str, "run_id": str(last_run.id) if last_run else None})).scalar() or 0

    # Best combination
    best_combo_result = await db.execute(
        select(ProfileRuleCombination)
        .where(
            ProfileRuleCombination.user_id == user_id,
            ProfileRuleCombination.run_id == (last_run.id if last_run else None),
        )
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
    ml_available = {
        "xgboost": find_spec("xgboost") is not None,
        "lightgbm": find_spec("lightgbm") is not None,
        "catboost": find_spec("catboost") is not None,
        "optuna": find_spec("optuna") is not None,
        "mlxtend": find_spec("mlxtend") is not None,
        "shap": find_spec("shap") is not None,
        "anthropic_sdk": find_spec("anthropic") is not None,
    }

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
        "manual_adjustments_pending": int(manual_counts["pending"] or 0),
        "manual_adjustments_applied": int(manual_counts["applied"] or 0),
        "manual_adjustments_runtime_pending": int(manual_counts["runtime_pending"] or 0),
        "manual_adjustments_runtime_confirmed": int(manual_counts["runtime_confirmed"] or 0),
        "manual_adjustments_rolled_back": int(manual_counts["rolled_back"] or 0),
        "base_win_rate": float(last_run.base_win_rate or 0) if last_run else None,
        "best_profile_name": best_profile_row.profile_name if best_profile_row else None,
        "best_profile_win_rate": round(best_profile_wr, 4) if best_profile_wr is not None else None,
        "best_combination_name": best_combo.suggested_name if best_combo else None,
        "best_combination_champion_score": float(best_combo.champion_score or 0) if best_combo else None,
        "last_run_lookback_days": last_run.lookback_days if last_run else None,
        "snapshot_info": {
            "is_snapshot": True,
            "source_table": "profile_intelligence_runs",
            "run_at": last_run.run_at.isoformat() if last_run else None,
            "lookback_days": last_run.lookback_days if last_run else None,
            "fields_from_snapshot": [
                "total_profiles_analyzed",
                "total_closed_trades",
                "base_win_rate",
            ],
            "note": (
                "These fields are computed by the PI Engine at run time and frozen. "
                "They do NOT reflect real-time shadow_trades state."
            ),
        } if last_run else None,
        "metric_contracts": {
            "win_rate_base": build_metric_contract(
                metric_id="overview.run_snapshot_win_rate",
                label="Win Rate Base trade-level (snapshot do run)",
                source_table="profile_intelligence_runs",
                aggregation_type="trade_level",
                aggregation_level="per_trade",
                formula="TP_HIT / (TP_HIT + SL_HIT + TIMEOUT)",
                window_label=f"{last_run.lookback_days}d lookback" if last_run else None,
                window_field="created_at (within run at time)",
                is_snapshot=True,
                snapshot_computed_at=last_run.run_at.isoformat() if last_run else None,
                not_comparable_with=["calibration.bucket_avg_win_rate"],
                warning=(
                    "Snapshot congelado do último run. "
                    "Não atualiza em tempo real. "
                    "Para dados live, consultar Shadow Portfolio."
                ),
            ),
            "suggestions_pending": build_metric_contract(
                metric_id="overview.run_suggestions_pending",
                label="Sugestões Pendentes do Run (profile_suggestions — tabela legada)",
                source_table="profile_suggestions",
                aggregation_type="count",
                aggregation_level="row",
                formula="COUNT(*) WHERE status = 'pending_user_approval'",
                window_label="all-time",
                filters={"status": "pending_user_approval"},
                unit="count",
                not_comparable_with=["calibration.suggestions_registered"],
                warning=(
                    "Usa profile_suggestions (tabela legada do PI Engine clássico). "
                    "NÃO é a mesma tabela que profile_adjustment_suggestions "
                    "(Calibration Live Engine, usada pelo Calibration Evolution)."
                ),
            ),
        },
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
        "ml_challengers": _ml_challenger_status(),
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
    settings_override, _ = _normalize_unimplemented_ml_flags(
        payload.settings_override
    )

    # Create the run record immediately so we can return the ID
    run = ProfileIntelligenceRun(
        user_id=user_id,
        lookback_days=payload.lookback_days,
        min_closed_trades=payload.min_closed_trades,
        status="queued",
        trigger_source="manual",
        engine_version=ProfileIntelligenceService.ENGINE_VERSION,
        settings_json=settings_override,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    run_id = run.id

    # Queue background execution
    from ..database import AsyncSessionLocal
    async def _run_background():
        async with AsyncSessionLocal() as bg_db:
            run_ok = False
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
                    settings_override=settings_override,
                )
                run_ok = True
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
            if run_ok:
                logger.info("[PI API] run %s completed — starting ML challenger training", run_id)
                try:
                    from ..tasks.profile_intelligence_job import _run_ml_challengers_if_enabled
                    async with AsyncSessionLocal() as ml_db:
                        await _run_ml_challengers_if_enabled(ml_db, user_id)
                    logger.info("[PI API] ML challenger training finished for run %s", run_id)
                except Exception as ml_exc:
                    logger.error("[PI API] ML challenger training failed for run %s: %s", run_id, ml_exc)

    background_tasks.add_task(_run_background)

    return RunResponse(run_id=str(run_id), status="queued", message="Run queued successfully")


@router.post("/train-ml")
async def trigger_ml_training(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Dispatch LightGBM + CatBoost training as a Celery task on the structural_compute queue.

    Requires enable_lightgbm and/or enable_catboost in the user's profile_intelligence config.
    Bypasses the PI Redis lock — runs training only, no PI analysis.
    """
    try:
        from ..tasks.profile_intelligence_job import train_ml_challengers_for_user
        task = train_ml_challengers_for_user.delay(str(user_id))
        logger.info("[PI API] train-ml dispatched task_id=%s user=%s", task.id, user_id)
        return {"status": "queued", "task_id": task.id, "queue": "structural_compute"}
    except Exception as exc:
        logger.error("[PI API] train-ml dispatch failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to dispatch ML training task: {exc}")


@router.post("/run-with-ml")
async def trigger_run_with_ml(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Dispatch PI run + ML challenger training as a Celery task on the structural queue.

    Runs _run_for_user on scalpyn-worker-structural, which includes:
      1. PI Engine analysis
      2. Autopilot cycle
      3. LightGBM (L1_SPECTRUM) + CatBoost (L3/L3_LAB) training if enabled in settings

    Returns immediately with the Celery task ID.
    """
    try:
        from ..tasks.profile_intelligence_job import run_for_user
        task = run_for_user.delay(str(user_id))
        logger.info("[PI API] run-with-ml dispatched task_id=%s user=%s", task.id, user_id)
        return {"status": "queued", "task_id": task.id, "queue": "structural"}
    except Exception as exc:
        logger.error("[PI API] run-with-ml dispatch failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to dispatch ML training task: {exc}")


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
          AND source IN ('L3', 'L3_LAB')
          AND {official_where('shadow_trades')}
        GROUP BY profile_id, profile_name, source
        HAVING COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) >= :min_ct
        ORDER BY
            (COUNT(*) FILTER (WHERE outcome = 'TP_HIT'))::float
            / GREATEST(COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')), 1) DESC
        LIMIT :limit
    """), {
        "uid": str(user_id),
        "min_ct": min_closed_trades,
        "limit": limit,
        **official_params(),
    })).fetchall()

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

    return {
        "profiles": profiles,
        "lookback_days": lookback_days,
        "dataset_version": DATASET_VERSION,
        "label_version": LABEL_VERSION,
    }


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
                ProfileIntelligenceRun.status == "completed",
                ProfileIntelligenceRun.engine_version == "2B.2-native-official",
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
    if role == "winning_indicator":
        q = q.where(ProfileIndicatorStats.avg_pnl_pct > 0)
    elif role == "losing_indicator":
        q = q.where(ProfileIndicatorStats.avg_pnl_pct < 0)
    if confidence_level:
        q = q.where(ProfileIndicatorStats.confidence_level == confidence_level)
    if indicator:
        q = q.where(ProfileIndicatorStats.indicator == indicator)
    if role == "losing_indicator":
        q = q.order_by(
            ProfileIndicatorStats.loss_rate.desc().nullslast(),
            ProfileIndicatorStats.lift_vs_base.asc().nullslast(),
        )
    else:
        q = q.order_by(ProfileIndicatorStats.lift_vs_base.desc().nullslast())
    q = q.limit(limit)
    stats = (await db.execute(q)).scalars().all()
    source_profile_ids = {
        UUID(str(profile_id))
        for stat in stats
        for profile_id in (stat.source_profile_ids or [])
        if profile_id
    }
    profile_names: dict[str, str] = {}
    if source_profile_ids:
        profile_rows = (await db.execute(
            select(Profile.id, Profile.name).where(
                Profile.user_id == user_id,
                Profile.id.in_(source_profile_ids),
            )
        )).all()
        profile_names = {str(row.id): row.name for row in profile_rows}

    indicators = []
    for stat in stats:
        item = _ind_to_dict(stat)
        item["associated_profiles"] = [
            {"id": str(profile_id), "name": profile_names.get(str(profile_id), str(profile_id))}
            for profile_id in (stat.source_profile_ids or [])
            if str(profile_id) in profile_names
        ]
        actionable = (
            stat.validation_status == "validated"
            and stat.actionability_status in {"validated", "positive_signal_candidate"}
            and bool(item["associated_profiles"])
        )
        item["can_apply_shadow_adjustment"] = actionable
        item["can_request_ai_review"] = (
            stat.validation_status == "validated"
            and stat.actionability_status == "ai_review_pending"
            and bool(item["associated_profiles"])
        )
        item["adjustment_blocked_reason"] = None if actionable else (
            stat.actionability_status
            or stat.validation_status
            or "missing_associated_profile"
        )
        indicators.append(item)
    return {
        "indicators": indicators,
        "role": role,
        "run_id": str(selected_run_id),
        "dataset_version": DATASET_VERSION,
        "label_version": LABEL_VERSION,
    }


@router.post("/indicators/{indicator_stat_id}/ai-review")
async def review_indicator_adjustment_with_ai(
    indicator_stat_id: str,
    payload: IndicatorShadowAdjustmentRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        stat_id = UUID(indicator_stat_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid indicator_stat_id") from exc

    indicator_stat = await db.scalar(select(ProfileIndicatorStats).where(
        ProfileIndicatorStats.id == stat_id,
        ProfileIndicatorStats.user_id == user_id,
    ))
    if indicator_stat is None:
        raise HTTPException(status_code=404, detail="Indicator statistic not found")

    latest_run_id = await db.scalar(select(ProfileIntelligenceRun.id).where(
        ProfileIntelligenceRun.user_id == user_id,
        ProfileIntelligenceRun.engine_version == "2B.2-native-official",
        ProfileIntelligenceRun.status.in_(("completed", "collection_in_progress")),
    ).order_by(ProfileIntelligenceRun.run_at.desc()).limit(1))
    if latest_run_id != indicator_stat.run_id:
        raise HTTPException(status_code=409, detail="indicator_not_from_latest_official_run")

    associated_ids = {UUID(str(value)) for value in (indicator_stat.source_profile_ids or [])}
    requested_ids = set(payload.profile_ids)
    if not requested_ids or not requested_ids.issubset(associated_ids):
        raise HTTPException(status_code=409, detail="profile_not_associated_with_indicator")
    profiles = list((await db.execute(select(Profile).where(
        Profile.user_id == user_id,
        Profile.id.in_(requested_ids),
        Profile.is_active.is_(True),
    ))).scalars().all())
    if {profile.id for profile in profiles} != requested_ids:
        raise HTTPException(status_code=409, detail="profile_not_owned_or_inactive")

    from ..services.profile_indicator_ai_review_service import review_indicator_adjustment
    try:
        review = await review_indicator_adjustment(
            db,
            user_id=user_id,
            indicator_stat=indicator_stat,
            profiles=profiles,
        )
        await log_pi_event(
            db,
            user_id,
            "indicator_ai_review_completed",
            run_id=indicator_stat.run_id,
            result_json={
                "indicator_stat_id": indicator_stat_id,
                "verdict": review["verdict"],
                "context_hash": review["context_hash"],
                "incumbent_mutated": False,
                "training_dataset_mutated": False,
            },
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        logger.exception("[PI API] indicator AI review failed stat=%s", indicator_stat_id)
        raise
    return {
        "indicator_stat_id": indicator_stat_id,
        "actionability_status": indicator_stat.actionability_status,
        "review": review,
        "incumbent_mutated": False,
        "training_dataset_mutated": False,
    }


@router.post("/indicators/{indicator_stat_id}/shadow-adjustment", status_code=201)
async def create_indicator_shadow_adjustment(
    indicator_stat_id: str,
    payload: IndicatorShadowAdjustmentRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    try:
        stat_id = UUID(indicator_stat_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid indicator_stat_id") from exc

    indicator_stat = await db.scalar(
        select(ProfileIndicatorStats).where(
            ProfileIndicatorStats.id == stat_id,
            ProfileIndicatorStats.user_id == user_id,
        )
    )
    if indicator_stat is None:
        raise HTTPException(status_code=404, detail="Indicator statistic not found")

    latest_run_id = await db.scalar(
        select(ProfileIntelligenceRun.id).where(
            ProfileIntelligenceRun.user_id == user_id,
            ProfileIntelligenceRun.engine_version == "2B.2-native-official",
            ProfileIntelligenceRun.status.in_(("completed", "collection_in_progress")),
        ).order_by(ProfileIntelligenceRun.run_at.desc()).limit(1)
    )
    if latest_run_id != indicator_stat.run_id:
        raise HTTPException(status_code=409, detail="indicator_not_from_latest_official_run")

    associated_ids = {UUID(str(value)) for value in (indicator_stat.source_profile_ids or [])}
    requested_ids = set(payload.profile_ids)
    if not requested_ids.issubset(associated_ids):
        raise HTTPException(status_code=409, detail="profile_not_associated_with_indicator")

    profiles = list((await db.execute(
        select(Profile).where(
            Profile.user_id == user_id,
            Profile.id.in_(requested_ids),
            Profile.is_active.is_(True),
        )
    )).scalars().all())
    if {profile.id for profile in profiles} != requested_ids:
        raise HTTPException(status_code=409, detail="profile_not_owned_or_inactive")

    from ..services.profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService

    service = ProfileIntelligenceAutopilotService()
    results = []
    try:
        for profile in profiles:
            candidate, created = await service.create_candidate_from_indicator_stat(
                db,
                user_id=user_id,
                indicator_stat=indicator_stat,
                base_profile=profile,
            )
            results.append({
                "profile_id": str(profile.id),
                "profile_name": profile.name,
                "candidate_id": str(candidate.id),
                "candidate_profile_id": str(candidate.profile_id),
                "state": candidate.state,
                "created": created,
            })
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        logger.exception("[PI API] indicator shadow adjustment failed stat=%s", indicator_stat_id)
        raise

    return {
        "indicator_stat_id": indicator_stat_id,
        "mode": "SHADOW_ONLY",
        "incumbent_mutated": False,
        "candidates": results,
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
    selected_run_id: Optional[UUID]
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
                ProfileIntelligenceRun.status == "completed",
                ProfileIntelligenceRun.engine_version == "2B.2-native-official",
            )
            .order_by(ProfileIntelligenceRun.run_at.desc())
            .limit(1)
        )
    if selected_run_id is None:
        return {"combinations": [], "run_id": None}
    q = select(ProfileRuleCombination).where(
        ProfileRuleCombination.user_id == user_id,
        ProfileRuleCombination.run_id == selected_run_id,
    )
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
    return {
        "combinations": [_combo_to_dict(c) for c in combos],
        "run_id": str(selected_run_id),
        "dataset_version": DATASET_VERSION,
        "label_version": LABEL_VERSION,
    }


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

    from ..services.profile_validation_service import suggestion_actionable
    validation_metrics = combo.validation_metrics_json or {}
    actionable, blocked_reason = suggestion_actionable(
        combo.combination_type,
        validation_metrics,
    )
    if validation_metrics.get("dataset_version") != DATASET_VERSION:
        actionable = False
        blocked_reason = "dataset_not_official_native"
    if validation_metrics.get("label_version") != LABEL_VERSION:
        actionable = False
        blocked_reason = "label_contract_not_official"
    if len(list(combo.source_profile_ids or [])) != 1:
        actionable = False
        blocked_reason = "ambiguous_profile_attribution"
    if not actionable:
        await log_pi_event(
            db,
            user_id,
            "suggestion_from_combination_blocked_validation",
            run_id=combo.run_id,
            combination_id=cid,
            event_description=(
                "Applicable suggestion blocked because out-of-sample "
                f"validation failed: {blocked_reason}"
            ),
            result_json={
                "source_type": combo.combination_type,
                "validation_status": validation_metrics.get(
                    "validation_status"
                ),
                "actionability_status": validation_metrics.get(
                    "actionability_status"
                ),
                "blocked_reason": blocked_reason,
                "mutation_applied": False,
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail=f"combination_not_actionable:{blocked_reason}",
        )

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
        "source_type": combo.combination_type,
        "validation_status": validation_metrics.get("validation_status"),
        "actionability_status": validation_metrics.get("actionability_status"),
        "blocked_reason": validation_metrics.get("blocked_reason"),
        "discovery_trade_count": (
            (combo.discovery_metrics_json or {}).get("total_cases", 0)
        ),
        "validation_trade_count": validation_metrics.get("total_cases", 0),
        "discovery_lift": (
            (combo.discovery_metrics_json or {}).get("lift")
        ),
        "validation_lift": validation_metrics.get("lift"),
    }

    suggested_signals = {"conditions": []} if not combo.signals_json else combo.signals_json
    suggested_scoring = combo.scoring_rules_json
    suggested_blocks = combo.block_rules_json or STANDARD_BLOCK_RULES
    source_profile_ids = list(combo.source_profile_ids or [])
    source_profiles = list(combo.source_profiles or [])
    if not source_profile_ids:
        raise HTTPException(
            status_code=409,
            detail="combination_not_actionable:missing_profile_id",
        )
    target_profile_id = UUID(str(source_profile_ids[0]))
    target_profile_name = (
        source_profiles[0] if source_profiles else str(target_profile_id)
    )

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
        source_type=combo.combination_type,
        source_run_id=combo.run_id,
        profile_id=target_profile_id,
        profile_name=target_profile_name,
        suggested_profile_name=profile_name,
        suggested_profile_description=f"Auto-generated from combination: {comb_name}",
        suggested_profile_family=family,
        source_profiles=source_profiles,
        source_profile_ids=source_profile_ids,
        target_section="profile",
        target_field="config",
        current_value=None,
        suggested_config_json={"source": "combination", "combination_id": str(cid)},
        suggested_signals_json=suggested_signals,
        suggested_scoring_json=suggested_scoring,
        suggested_block_rules_json=suggested_blocks,
        proposed_value={
            "signals": suggested_signals,
            "scoring": suggested_scoring,
            "block_rules": suggested_blocks,
        },
        diff_json={
            "before": None,
            "after": {
                "signals": suggested_signals,
                "scoring": suggested_scoring,
                "block_rules": suggested_blocks,
            },
            "target": "new_shadow_profile",
        },
        evidence_summary_json=evidence,
        quantitative_explanation=quant_explanation,
        confidence_score=float(combo.champion_score or 0),
        confidence_level=confidence_level,
        confidence=float(combo.champion_score or 0),
        lift=validation_metrics.get("lift"),
        evidence_count=validation_metrics.get("total_cases", 0),
        expected_impact={
            "validation_expected_pnl": validation_metrics.get("expected_pnl"),
            "validation_win_rate_lift": (
                float(validation_metrics.get("win_rate", 0) or 0)
                - float(validation_metrics.get("base_win_rate", 0) or 0)
            ),
        },
        risk_level="high" if combo.overfit_risk else "medium",
        validation_status="validated",
        actionability_status=validation_metrics.get(
            "actionability_status",
            "validated",
        ),
        rollback_payload={
            "action": "archive_generated_profile",
            "source_combination_id": str(cid),
        },
        dataset_version=f"{DATASET_VERSION}:{combo.run_id}",
        feature_schema_version="entry_features_v2",
        label_version=LABEL_VERSION,
        status="validated",
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
    selected_run_id: Optional[UUID]
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
                ProfileIntelligenceRun.status == "completed",
                ProfileIntelligenceRun.engine_version == "2B.2-native-official",
            )
            .order_by(ProfileIntelligenceRun.run_at.desc())
            .limit(1)
        )
    if selected_run_id is None:
        return {"suggestions": [], "run_id": None}
    q = select(ProfileSuggestion).where(
        ProfileSuggestion.user_id == user_id,
        ProfileSuggestion.run_id == selected_run_id,
    )
    if status:
        q = q.where(ProfileSuggestion.status == status)
    if confidence_level:
        q = q.where(ProfileSuggestion.confidence_level == confidence_level)
    if family:
        q = q.where(ProfileSuggestion.suggested_profile_family == family)
    q = q.order_by(ProfileSuggestion.confidence_score.desc().nullslast()).limit(limit)
    suggestions = (await db.execute(q)).scalars().all()
    return {
        "suggestions": [_sugg_to_dict(s) for s in suggestions],
        "run_id": str(selected_run_id),
    }


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


# ── 12b. Consolidated mutations timeline ──────────────────────────────────────

@router.get("/mutations")
async def get_mutations_timeline(
    profile_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Unified mutation timeline combining all audit sources.

    Sources (in order of richness):
    1. autopilot_audit_logs — full before/after/diff, explicit mutation_applied
    2. profile_adjustment_versions — shadow calibration diffs (buy_threshold)
    3. profile_intelligence_audit_log — AI suggestion events with diff_json
    """
    profile_filter_sql = ""
    profile_filter_params: dict = {
        "uid": str(user_id),
        "limit": limit,
    }
    if profile_id:
        try:
            profile_filter_params["pid"] = str(UUID(profile_id))
            profile_filter_sql = "AND a.profile_id = CAST(:pid AS uuid)"
        except ValueError:
            pass

    # Source 1: autopilot_audit_logs
    autopilot_rows = (await db.execute(text(f"""
        SELECT
            'autopilot' AS source,
            a.id::text          AS id,
            a.profile_id::text  AS profile_id,
            a.profile_name,
            a.action            AS event_type,
            a.reason            AS event_description,
            a.reason_code       AS mutation_action,
            a.mutation_applied,
            COALESCE(a.mutation_status,
                CASE WHEN a.mutation_applied THEN 'APPLIED_TO_PROFILE_CONFIG'
                     WHEN a.dry_run THEN 'DRY_RUN_ONLY'
                     ELSE 'APPLIED_TO_SHADOW' END
            )                   AS mutation_status,
            a.dry_run,
            a.config_before     AS before_json,
            a.config_after      AS after_json,
            a.diff_json,
            a.perf_snapshot     AS evidence_json,
            a.trigger_source,
            a.created_at
        FROM autopilot_audit_logs a
        WHERE a.user_id = CAST(:uid AS uuid)
        {profile_filter_sql}
        ORDER BY a.created_at DESC
        LIMIT :limit
    """), profile_filter_params)).mappings().all()

    # Source 2: profile_adjustment_versions (shadow calibration)
    shadow_filter = " AND v.profile_id = CAST(:pid AS uuid)" if profile_id else ""
    shadow_params: dict = {"uid": str(user_id), "limit": limit}
    if profile_id:
        shadow_params["pid"] = str(UUID(profile_id)) if profile_id else None

    shadow_rows = (await db.execute(text(f"""
        SELECT
            'shadow_calibration' AS source,
            v.id::text           AS id,
            v.profile_id::text   AS profile_id,
            NULL                 AS profile_name,
            'BUY_THRESHOLD_UPDATED' AS event_type,
            'Shadow calibration adjustment'  AS event_description,
            'BUY_THRESHOLD_UPDATED'          AS mutation_action,
            v.mutation_applied,
            CASE WHEN v.mutation_applied THEN 'APPLIED_TO_PROFILE_CONFIG'
                 ELSE 'APPLIED_TO_SHADOW' END AS mutation_status,
            false                AS dry_run,
            v.before_snapshot    AS before_json,
            v.after_snapshot     AS after_json,
            v.diff               AS diff_json,
            NULL                 AS evidence_json,
            NULL                 AS trigger_source,
            v.created_at
        FROM profile_adjustment_versions v
        WHERE v.profile_id IN (
            SELECT id FROM profiles WHERE user_id = CAST(:uid AS uuid)
        )
        {shadow_filter}
        ORDER BY v.created_at DESC
        LIMIT :limit
    """), shadow_params)).mappings().all()

    # Source 3: profile_intelligence_audit_log (events with diff)
    pi_filter = " AND l.profile_id = CAST(:pid AS uuid)" if profile_id else ""
    pi_params: dict = {"uid": str(user_id), "limit": limit}
    if profile_id:
        pi_params["pid"] = str(UUID(profile_id)) if profile_id else None

    pi_rows = (await db.execute(text(f"""
        SELECT
            'pi_engine' AS source,
            l.id::text  AS id,
            l.profile_id::text AS profile_id,
            l.profile_name,
            l.event_type,
            l.event_description,
            l.event_type    AS mutation_action,
            l.mutation_applied,
            l.mutation_status,
            l.dry_run,
            l.before_json,
            l.after_json,
            l.diff_json,
            l.payload_json  AS evidence_json,
            NULL            AS trigger_source,
            l.created_at
        FROM profile_intelligence_audit_log l
        WHERE l.user_id = CAST(:uid AS uuid)
          AND l.diff_json IS NOT NULL
        {pi_filter}
        ORDER BY l.created_at DESC
        LIMIT :limit
    """), pi_params)).mappings().all()

    def _row_to_event(row: dict) -> dict:
        diff = row.get("diff_json")
        if isinstance(diff, str):
            try:
                diff = json.loads(diff)
            except Exception:
                diff = None
        before = row.get("before_json")
        if isinstance(before, str):
            try:
                before = json.loads(before)
            except Exception:
                before = None
        after = row.get("after_json")
        if isinstance(after, str):
            try:
                after = json.loads(after)
            except Exception:
                after = None
        evidence = row.get("evidence_json")
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except Exception:
                evidence = None
        return {
            "source": row["source"],
            "id": row["id"],
            "profile_id": row.get("profile_id"),
            "profile_name": row.get("profile_name"),
            "event_type": row.get("event_type"),
            "event_description": row.get("event_description"),
            "mutation_action": row.get("mutation_action"),
            "mutation_applied": row.get("mutation_applied"),
            "mutation_status": row.get("mutation_status"),
            "dry_run": row.get("dry_run"),
            "diff_json": diff,
            "before_json": before,
            "after_json": after,
            "evidence_json": evidence,
            "trigger_source": row.get("trigger_source"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    events = sorted(
        [_row_to_event(dict(r)) for r in list(autopilot_rows) + list(shadow_rows) + list(pi_rows)],
        key=lambda e: e["created_at"] or "",
        reverse=True,
    )[:limit]

    return {"events": events, "total": len(events)}


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
    settings, warnings = _normalize_unimplemented_ml_flags(settings)
    return {
        "settings": settings,
        "warnings": warnings,
        "ml_challengers": _ml_challenger_status(),
    }


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
    new_settings, warnings = _normalize_unimplemented_ml_flags(new_settings)

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

    return {
        "settings": new_settings,
        "warnings": warnings,
        "ml_challengers": _ml_challenger_status(),
    }


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


@router.post("/autopilot/run-cycle")
async def trigger_autopilot_run_cycle(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Executa apenas o ciclo Auto-Pilot, sem análise PI completa."""
    from ..services.profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService
    row, _ = await ProfileIntelligenceAutopilotService().get_settings(db, user_id)
    if not row.enabled:
        raise HTTPException(status_code=409, detail="Auto-Pilot global está desligado")
    await db.commit()
    try:
        from ..tasks.profile_intelligence_job import run_cycle_for_user
        task = run_cycle_for_user.delay(str(user_id))
        return {"status": "queued", "task_id": task.id}
    except Exception as exc:
        logger.warning("[PI API] run-cycle dispatch failed, using background: %s", exc)
        from ..database import AsyncSessionLocal

        async def _background():
            async with AsyncSessionLocal() as bg_db:
                await ProfileIntelligenceAutopilotService().run_cycle(bg_db, user_id, analysis_run_id=None, force=True)

        background_tasks.add_task(_background)
        return {"status": "queued", "task_id": None}


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
        "approval_status": candidate.approval_status,
        "approval_required": candidate.approval_required,
        "approved_by": str(candidate.approved_by) if candidate.approved_by else None,
        "approved_at": candidate.approved_at.isoformat() if candidate.approved_at else None,
        "approval_reason": candidate.approval_reason,
        "approval_source": candidate.approval_source,
        "promotion_blocked_reason": candidate.promotion_blocked_reason,
        "rollback_available": bool(candidate.rollback_payload),
        "rollback_payload": candidate.rollback_payload,
        "reason": candidate.decision_reason,
        "evidence": candidate.evidence_json or {},
        "created_at": candidate.created_at.isoformat(),
        "updated_at": candidate.updated_at.isoformat(),
    } for candidate, profile_name, watchlist_name in rows]}


@router.post("/autopilot/candidates/{candidate_id}/approve")
async def approve_autopilot_candidate(
    candidate_id: UUID,
    payload: CandidateApprovalRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..services.profile_intelligence_autopilot_service import (
        ProfileIntelligenceAutopilotService,
    )

    result = await ProfileIntelligenceAutopilotService().approve_candidate_for_live(
        db,
        user_id,
        candidate_id,
        approved_by=payload.approved_by,
        approval_reason=payload.approval_reason,
        approval_source=payload.approval_source,
        confirm_risk=payload.confirm_risk,
    )
    if result.get("status") == "blocked":
        raise HTTPException(status_code=409, detail=result["reason"])
    return result


@router.post("/autopilot/candidates/{candidate_id}/reject")
async def reject_autopilot_candidate(
    candidate_id: UUID,
    payload: CandidateRejectionRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..services.profile_intelligence_autopilot_service import (
        ProfileIntelligenceAutopilotService,
    )

    result = await ProfileIntelligenceAutopilotService().reject_candidate(
        db,
        user_id,
        candidate_id,
        rejected_by=user_id,
        rejection_reason=payload.rejection_reason,
    )
    if result.get("status") == "blocked":
        raise HTTPException(status_code=409, detail=result["reason"])
    return result


@router.post("/autopilot/candidates/{candidate_id}/activate")
async def activate_autopilot_candidate(
    candidate_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..services.profile_intelligence_autopilot_service import (
        ProfileIntelligenceAutopilotService,
    )

    result = await ProfileIntelligenceAutopilotService().activate_approved_candidate(
        db,
        user_id,
        candidate_id,
        activated_by=user_id,
    )
    if result.get("status") == "blocked":
        raise HTTPException(status_code=409, detail=result["reason"])
    return result


@router.post("/autopilot/candidates/{candidate_id}/rollback")
async def rollback_autopilot_candidate(
    candidate_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    from ..services.profile_intelligence_autopilot_service import (
        ProfileIntelligenceAutopilotService,
    )

    result = await ProfileIntelligenceAutopilotService().rollback_candidate(
        db,
        user_id,
        candidate_id,
        rolled_back_by=user_id,
    )
    if result.get("status") == "blocked":
        raise HTTPException(status_code=409, detail=result["reason"])
    return result


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

@router.get("/governance/models")
async def list_governed_models(
    status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    query = (
        select(MLModelRegistry)
        .outerjoin(Profile, Profile.id == MLModelRegistry.profile_id)
        .where(
            (MLModelRegistry.profile_id.is_(None))
            | (Profile.user_id == user_id)
        )
    )
    if status:
        query = query.where(MLModelRegistry.status == status)
    models = (await db.execute(
        query.order_by(MLModelRegistry.created_at.desc()).limit(limit)
    )).scalars().all()
    champions = (await db.execute(
        select(ProductionChampionControl)
        .join(Profile, Profile.id == ProductionChampionControl.profile_id)
        .where(Profile.user_id == user_id)
    )).scalars().all()
    active_ids = {str(row.active_model_id) for row in champions}
    return {
        "models": [{
            "model_id": str(row.model_id),
            "source_ml_model_id": (
                str(row.source_ml_model_id) if row.source_ml_model_id else None
            ),
            "model_type": row.model_type,
            "model_version": row.model_version,
            "profile_id": str(row.profile_id) if row.profile_id else None,
            "profile_name": row.profile_name,
            "strategy_skill": row.strategy_skill,
            "market_regime": row.market_regime,
            "dataset_version": row.dataset_version,
            "feature_schema_version": row.feature_schema_version,
            "label_version": row.label_version,
            "metrics_json": row.metrics_json or {},
            "threshold": float(row.threshold) if row.threshold is not None else None,
            "status": row.status,
            "is_active_production_champion": str(row.model_id) in active_ids,
            "artifact_path": row.artifact_path,
            "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None,
        } for row in models],
        "production_champions": [{
            "profile_id": str(row.profile_id),
            "market_regime": row.market_regime,
            "strategy_skill": row.strategy_skill,
            "active_model_id": str(row.active_model_id),
            "active_model_type": row.active_model_type,
            "active_threshold": float(row.active_threshold),
            "rollback_available": row.rollback_available,
        } for row in champions],
        "supported_model_types": {
            "xgboost": {"implemented": True, "operational": True},
            "lightgbm": {"implemented": False, "operational": False},
            "catboost": {"implemented": False, "operational": False},
        },
    }


@router.get("/governance/forward-validations")
async def list_forward_validations(
    profile_id: Optional[UUID] = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    query = (
        select(AlgorithmForwardValidation)
        .join(Profile, Profile.id == AlgorithmForwardValidation.profile_id)
        .where(Profile.user_id == user_id)
    )
    if profile_id:
        query = query.where(AlgorithmForwardValidation.profile_id == profile_id)
    rows = (await db.execute(
        query.order_by(AlgorithmForwardValidation.created_at.desc()).limit(limit)
    )).scalars().all()
    return {"forward_validations": [{
        "id": str(row.id),
        "suggestion_id": str(row.suggestion_id) if row.suggestion_id else None,
        "model_id": str(row.model_id) if row.model_id else None,
        "profile_id": str(row.profile_id),
        "stage": row.stage,
        "validation_status": row.validation_status,
        "metrics_json": row.metrics_json or {},
        "human_approved": bool(row.human_approved_by and row.human_approved_at),
        "rollback_available": bool(row.rollback_payload),
        "blocked_reason": row.blocked_reason,
    } for row in rows]}


@router.get("/governance/autonomy-policy")
async def get_autonomy_policy(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    policy = (await db.execute(
        select(AutopilotAutonomyPolicy).where(
            AutopilotAutonomyPolicy.user_id == user_id
        )
    )).scalars().first()
    if policy is None:
        return {
            "maximum_level": 2,
            "state": "suggest_or_shadow_only",
            "levels_4_5_enabled": False,
            "auto_rollback_enabled": False,
            "source": "safe_default",
        }
    return {
        "maximum_level": policy.maximum_level,
        "impact_limit_json": policy.impact_limit_json or {},
        "cooldown_seconds": policy.cooldown_seconds,
        "max_changes_per_day": policy.max_changes_per_day,
        "risk_budget_json": policy.risk_budget_json or {},
        "post_change_monitoring": policy.post_change_monitoring,
        "auto_rollback_enabled": policy.auto_rollback_enabled,
        "levels_4_5_enabled": False,
        "source": "database",
    }


@router.get("/calibration/versions")
async def list_calibration_versions(
    status: Optional[str] = Query(None, description="Filter by shadow_validation_status"),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """List profile_adjustment_versions, optionally filtered by validation status."""
    where_clause = "WHERE v.shadow_validation_status = :status" if status else ""
    rows = await db.execute(text(f"""
        SELECT v.id, v.suggestion_id, v.profile_id,
               p.name AS profile_name,
               v.version_status, v.shadow_validation_status,
               v.mutation_applied, v.rollback_available,
               v.win_rate_before, v.win_rate_after, v.validation_reason,
               v.validated_at, v.applied_at, v.applied_by,
               v.before_snapshot, v.after_snapshot, v.diff,
               v.created_at
        FROM profile_adjustment_versions v
        LEFT JOIN profiles p ON p.id = v.profile_id
        {where_clause}
        ORDER BY v.created_at DESC
        LIMIT :limit
    """), {"status": status, "limit": limit} if status else {"limit": limit})
    versions = rows.fetchall()
    return {
        "versions": [
            {
                "id": str(r.id),
                "suggestion_id": str(r.suggestion_id),
                "profile_id": str(r.profile_id),
                "profile_name": r.profile_name,
                "version_status": r.version_status,
                "shadow_validation_status": r.shadow_validation_status,
                "mutation_applied": r.mutation_applied,
                "rollback_available": r.rollback_available,
                "win_rate_before": float(r.win_rate_before) if r.win_rate_before is not None else None,
                "win_rate_after": float(r.win_rate_after) if r.win_rate_after is not None else None,
                "validation_reason": r.validation_reason,
                "validated_at": r.validated_at.isoformat() if r.validated_at else None,
                "applied_at": r.applied_at.isoformat() if r.applied_at else None,
                "applied_by": r.applied_by,
                "before_snapshot": r.before_snapshot,
                "after_snapshot": r.after_snapshot,
                "diff": r.diff,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in versions
        ],
        "count": len(versions),
    }


@router.post("/calibration/versions/{version_id}/apply")
async def apply_calibration_version(
    version_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Block the retired endpoint that mutated an incumbent in place.

    Calibration v2 creates an immutable challenger and promotes only a
    versioned profile after shadow evidence and human approval.
    """
    raise HTTPException(
        status_code=409,
        detail={
            "code": "LEGACY_IN_PLACE_MUTATION_BLOCKED",
            "message": "A versão validada deve seguir o fluxo versionado Recommendation → Proposal → Shadow.",
            "next_endpoint": "/api/calibration-evolution/v2/recommendations",
            "mutation_applied": False,
        },
    )

    # Kept unreachable only for backwards-compatible source context during the
    # transition; the guard above is fail-closed and performs no database write.
    # Load PAV
    pav_row = await db.execute(text("""
        SELECT v.id, v.suggestion_id, v.profile_id,
               v.shadow_validation_status, v.mutation_applied,
               v.after_snapshot, v.diff
        FROM profile_adjustment_versions v
        WHERE v.id = :vid
    """), {"vid": version_id})
    pav = pav_row.fetchone()

    if pav is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if pav.shadow_validation_status != "VALIDATED":
        raise HTTPException(
            status_code=422,
            detail=f"Version is not VALIDATED (current: {pav.shadow_validation_status})"
        )
    if pav.mutation_applied:
        raise HTTPException(status_code=422, detail="Already applied")

    # Check PAS has requires_human_approval=true (set by run_shadow_validation_cycle)
    sugg_row = await db.execute(text("""
        SELECT requires_human_approval FROM profile_adjustment_suggestions WHERE id = :sid
    """), {"sid": str(pav.suggestion_id)})
    sugg = sugg_row.fetchone()
    if sugg is None or not sugg.requires_human_approval:
        raise HTTPException(
            status_code=422,
            detail="Linked suggestion does not have requires_human_approval=true"
        )

    # Apply diff to profile config — surgically update scoring.thresholds.buy
    after_snap = pav.after_snapshot or {}
    scoring_after = after_snap.get("scoring", {})
    thresholds_after = scoring_after.get("thresholds", {})
    new_buy = thresholds_after.get("buy")

    if new_buy is None:
        raise HTTPException(status_code=422, detail="after_snapshot missing scoring.thresholds.buy")

    # Fetch previous config for audit
    prof_row = await db.execute(text("SELECT name, config FROM profiles WHERE id = :pid"), {"pid": str(pav.profile_id)})
    prof_data = prof_row.fetchone()
    prof_name = prof_data.name if prof_data else "Unknown"
    prev_config = prof_data.config if prof_data else {}
    
    new_config = dict(prev_config)
    if new_buy is not None:
        if "scoring" not in new_config: new_config["scoring"] = {}
        if "thresholds" not in new_config["scoring"]: new_config["scoring"]["thresholds"] = {}
        new_config["scoring"]["thresholds"]["buy"] = new_buy

    await db.execute(text("""
        UPDATE profiles
        SET config = jsonb_set(config, '{scoring,thresholds,buy}', :new_buy::jsonb, true)
        WHERE id = :pid
    """), {"new_buy": str(new_buy), "pid": str(pav.profile_id)})

    applied_by_label = f"human:{user_id}"

    import uuid
    # Insert audit trail
    action_detail = f"Manually applied shadow validation mutation to profile '{prof_name}'. Buy threshold changed to {new_buy}."
    await db.execute(text("""
        INSERT INTO profile_audit_log (id, user_id, profile_id, changed_by, change_source, change_description, previous_config, new_config, created_at)
        VALUES (:id, :uid, :pid, :cb, 'Manual Human Calibration', :desc, :prev, :new_c, now())
    """), {
        "id": str(uuid.uuid4()),
        "uid": str(user_id),
        "pid": str(pav.profile_id),
        "cb": str(user_id),
        "desc": action_detail,
        "prev": json.dumps(prev_config),
        "new_c": json.dumps(new_config)
    })

    # Mark PAV as applied
    await db.execute(text("""
        UPDATE profile_adjustment_versions
        SET mutation_applied = true,
            applied_at       = now(),
            applied_by       = :applied_by
        WHERE id = :vid
    """), {"vid": version_id, "applied_by": applied_by_label})

    # Mark PAS as applied
    await db.execute(text("""
        UPDATE profile_adjustment_suggestions
        SET mutation_applied = true, updated_at = now()
        WHERE id = :sid
    """), {"sid": str(pav.suggestion_id)})

    # Close APA
    await db.execute(text("""
        UPDATE autopilot_pending_actions
        SET mutation_applied = true,
            action_status    = 'COMPLETED',
            updated_at       = now()
        WHERE suggestion_id = :sid
    """), {"sid": str(pav.suggestion_id)})

    await db.commit()
    return {
        "version_id": version_id,
        "profile_id": str(pav.profile_id),
        "mutation_applied": True,
        "applied_by": applied_by_label,
        "new_buy_threshold": new_buy,
    }


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
        "trigger_source": r.trigger_source,
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
        "source_profile_ids": s.source_profile_ids,
        "validation_status": s.validation_status,
        "actionability_status": s.actionability_status,
        "target_section": s.target_section,
        "evidence_json": s.evidence_json,
    }


def _combo_autopilot_decision(c: ProfileRuleCombination) -> str:
    """Derive autopilot decision from existing combination metrics (no new columns required)."""
    vm = c.validation_metrics_json or {}
    blocked_reason = vm.get("blocked_reason") or vm.get("actionability_status")
    validation_status = vm.get("validation_status")

    if validation_status == "validated":
        return MutationStatus.AUTO_APPROVED_FOR_SHADOW
    if blocked_reason and blocked_reason in BLOCKED_REASON_TO_DECISION:
        return BLOCKED_REASON_TO_DECISION[blocked_reason]
    if not vm:
        return MutationStatus.AUTO_ARCHIVED_HYPOTHESIS
    return MutationStatus.AUTO_ARCHIVED_HYPOTHESIS


def _combo_to_dict(c: ProfileRuleCombination) -> dict:
    return {
        "id": str(c.id), "run_id": str(c.run_id),
        "combination_hash": c.combination_hash,
        "combination_type": c.combination_type, "setup_family": c.setup_family,
        "suggested_name": c.suggested_name, "rules_json": c.rules_json,
        "signals_json": c.signals_json,
        "block_rules_json": c.block_rules_json,
        "source_profiles": c.source_profiles,
        "source_profile_ids": c.source_profile_ids,
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
        "autopilot_decision": _combo_autopilot_decision(c),
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _sugg_to_dict(s: ProfileSuggestion) -> dict:
    return {
        "id": str(s.id), "run_id": str(s.run_id),
        "source_combination_id": str(s.source_combination_id) if s.source_combination_id else None,
        "suggested_profile_name": s.suggested_profile_name,
        "suggested_profile_description": s.suggested_profile_description,
        "suggested_profile_family": s.suggested_profile_family,
        "source_type": s.source_type,
        "source_model_type": s.source_model_type,
        "source_model_id": str(s.source_model_id) if s.source_model_id else None,
        "source_run_id": str(s.source_run_id) if s.source_run_id else None,
        "profile_id": str(s.profile_id) if s.profile_id else None,
        "profile_name": s.profile_name,
        "source_profiles": s.source_profiles,
        "source_profile_ids": s.source_profile_ids,
        "target_section": s.target_section,
        "target_field": s.target_field,
        "current_value": s.current_value,
        "proposed_value": s.proposed_value,
        "diff_json": s.diff_json,
        "suggested_config_json": s.suggested_config_json,
        "suggested_signals_json": s.suggested_signals_json,
        "suggested_block_rules_json": s.suggested_block_rules_json,
        "evidence_summary_json": s.evidence_summary_json,
        "quantitative_explanation": s.quantitative_explanation,
        "ai_explanation": s.ai_explanation,
        "risk_notes": s.risk_notes,
        "confidence_score": float(s.confidence_score or 0),
        "confidence_level": s.confidence_level, "status": s.status,
        "confidence": float(s.confidence or 0) if s.confidence is not None else None,
        "lift": float(s.lift or 0) if s.lift is not None else None,
        "evidence_count": s.evidence_count,
        "expected_impact": s.expected_impact,
        "risk_level": s.risk_level,
        "validation_status": s.validation_status,
        "actionability_status": s.actionability_status,
        "blocked_reason": s.blocked_reason,
        "rollback_available": bool(s.rollback_payload),
        "rollback_payload": s.rollback_payload,
        "dataset_version": s.dataset_version,
        "feature_schema_version": s.feature_schema_version,
        "label_version": s.label_version,
        "applied_at": s.applied_at.isoformat() if s.applied_at else None,
        "reverted_at": s.reverted_at.isoformat() if s.reverted_at else None,
        "reason": s.reason,
        "created_profile_id": str(s.created_profile_id) if s.created_profile_id else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _audit_to_dict(r: ProfileIntelligenceAuditLog) -> dict:
    return {
        "id": str(r.id),
        "event_type": r.event_type,
        "event_description": r.event_description,
        "run_id": str(r.run_id) if r.run_id else None,
        "suggestion_id": str(r.suggestion_id) if r.suggestion_id else None,
        "combination_id": str(r.combination_id) if r.combination_id else None,
        "profile_id": str(r.profile_id) if r.profile_id else None,
        "profile_name": r.profile_name,
        "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
        "source_run_id": str(r.source_run_id) if r.source_run_id else None,
        "model_provider": r.model_provider,
        "model_name": r.model_name,
        "payload_json": r.payload_json,
        "result_json": r.result_json,
        "before_json": r.before_json,
        "after_json": r.after_json,
        "diff_json": r.diff_json,
        "mutation_applied": r.mutation_applied,
        "mutation_status": r.mutation_status,
        "dry_run": r.dry_run,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
