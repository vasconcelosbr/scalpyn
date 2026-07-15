"""Versioned Calibration Evolution API.

Writes are limited to evidence, recommendation, and immutable shadow proposal
records. No endpoint in this router mutates a champion profile configuration.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..ml.evidence_registry import publish_evidence
from ..services.calibration_orchestrator_v2 import calibration_orchestrator_v2
from ..services.ev_score_v2 import ev_score_v2_service
from ..services.config_service import config_service
from .config import get_current_user_id


router = APIRouter(prefix="/api/calibration-evolution/v2", tags=["calibration-evolution-v2"])


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, UUID)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class EvidenceRequest(BaseModel):
    source_type: Literal["MATH", "OPTUNA"]
    payload: dict[str, Any]


class RecommendationRequest(BaseModel):
    profile_id: UUID
    base_profile_version_id: UUID
    recommendation_type: Literal[
        "ADD_BLOCK_RULE", "UPDATE_THRESHOLD", "UPDATE_WEIGHT", "REMOVE_RULE"
    ]
    target_path: str
    current_value: Any
    proposed_value: Any
    bounded_change: dict[str, Any]
    evidence_refs: list[UUID] = Field(min_length=2)
    expected_impact: dict[str, Any]
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    confidence: float = Field(ge=0, le=1)
    validation_required: str
    rollback_condition: str
    cycle_id: UUID | None = None


class EVRefreshRequest(BaseModel):
    window_from: datetime
    window_to: datetime


async def _require_flag(
    db: AsyncSession, user_id: UUID, flag: str
) -> None:
    ml_config = await config_service.get_config(db, "ml", user_id)
    if ml_config.get(flag) is not True:
        raise HTTPException(
            409,
            detail={"code": "FEATURE_FLAG_DISABLED", "flag": flag, "mutation_applied": False},
        )


@router.post("/evidence", status_code=201)
async def create_evidence(
    request: EvidenceRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _require_flag(db, user_id, "calibration_evidence_registry_v1")
    payload = dict(request.payload)
    try:
        profile_id = UUID(str(payload["profile_id"]))
        profile_version_id = UUID(str(payload["profile_version_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, detail="profile_and_version_scope_required") from exc
    owned = await db.scalar(text("""
        SELECT EXISTS (
            SELECT 1
              FROM profiles p
              JOIN profile_versions pv ON pv.profile_id = p.id
             WHERE p.id = :profile_id
               AND p.user_id = :user_id
               AND pv.id = :profile_version_id
        )
    """), {
        "profile_id": str(profile_id), "user_id": str(user_id),
        "profile_version_id": str(profile_version_id),
    })
    if not owned:
        raise HTTPException(404, detail="profile_version_not_found")
    result = await publish_evidence(db, source_type=request.source_type, payload=payload)
    if not result.get("published"):
        await db.rollback()
        raise HTTPException(422, detail=result)
    await db.commit()
    return result


@router.post("/recommendations", status_code=201)
async def create_recommendation(
    request: RecommendationRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _require_flag(db, user_id, "calibration_orchestrator_v1")
    try:
        result = await calibration_orchestrator_v2.create_recommendation(
            db, user_id=user_id, **request.model_dump()
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, detail=str(exc)) from exc


@router.post("/recommendations/{recommendation_id}/proposal", status_code=201)
async def create_shadow_proposal(
    recommendation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _require_flag(db, user_id, "autopilot_calibration_v1")
    try:
        result = await calibration_orchestrator_v2.create_shadow_proposal(
            db, user_id=user_id, recommendation_id=recommendation_id
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/overview")
async def overview(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    row = (await db.execute(text("""
        SELECT
          (SELECT count(*) FROM calibration_recommendations WHERE user_id = :uid) recommendations,
          (SELECT count(*) FROM calibration_proposals WHERE user_id = :uid) proposals,
          (SELECT count(*) FROM calibration_proposals WHERE user_id = :uid AND state = 'SHADOW_CANARY') shadow_canaries,
          (SELECT count(*) FROM calibration_state_events WHERE user_id = :uid) state_events,
          (SELECT count(*) FROM profile_version_ev_scores e JOIN profiles p ON p.id=e.profile_id WHERE p.user_id=:uid) profile_ev_rows,
          (SELECT count(*) FROM crypto_profile_ev_scores e JOIN profiles p ON p.id=e.profile_id WHERE p.user_id=:uid) crypto_ev_rows
    """), {"uid": str(user_id)})).mappings().one()
    return _jsonable(dict(row))


@router.get("/recommendations")
async def list_recommendations(
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    rows = (await db.execute(text("""
        SELECT r.*, p.name AS profile_name
          FROM calibration_recommendations r
          JOIN profiles p ON p.id = r.profile_id
         WHERE r.user_id = :uid AND (:status IS NULL OR r.status = :status)
         ORDER BY r.created_at DESC LIMIT :limit
    """), {"uid": str(user_id), "status": status, "limit": limit})).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}


@router.get("/proposals")
async def list_proposals(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    rows = (await db.execute(text("""
        SELECT cp.*, p.name AS profile_name, c.state AS candidate_state,
               c.observed_trades, c.observed_win_rate, c.observed_avg_pnl_pct
          FROM calibration_proposals cp
          JOIN profiles p ON p.id = cp.profile_id
          LEFT JOIN profile_intelligence_autopilot_candidates c
            ON c.id = cp.autopilot_candidate_id
         WHERE cp.user_id = :uid
         ORDER BY cp.created_at DESC LIMIT :limit
    """), {"uid": str(user_id), "limit": limit})).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}


@router.get("/timeline")
async def timeline(
    profile_id: UUID | None = None,
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    rows = (await db.execute(text("""
        SELECT e.*, p.name AS profile_name
          FROM calibration_state_events e
          JOIN profiles p ON p.id = e.profile_id
         WHERE e.user_id = :uid
           AND (:profile_id IS NULL OR e.profile_id = :profile_id)
         ORDER BY e.created_at DESC LIMIT :limit
    """), {
        "uid": str(user_id),
        "profile_id": str(profile_id) if profile_id else None,
        "limit": limit,
    })).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}


@router.get("/profile-ev")
async def profile_ev(
    profile_id: UUID | None = None,
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    rows = (await db.execute(text("""
        SELECT e.*, p.name AS profile_name, pv.version_number, pv.status AS version_status
          FROM profile_version_ev_scores e
          JOIN profiles p ON p.id = e.profile_id
          JOIN profile_versions pv ON pv.id = e.profile_version_id
         WHERE p.user_id = :uid
           AND (:profile_id IS NULL OR e.profile_id = :profile_id)
         ORDER BY e.computed_at DESC LIMIT :limit
    """), {
        "uid": str(user_id), "profile_id": str(profile_id) if profile_id else None,
        "limit": limit,
    })).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}


@router.post("/ev/refresh")
async def refresh_contextual_ev(
    request: EVRefreshRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _require_flag(db, user_id, "ev_score_v2")
    try:
        result = await ev_score_v2_service.refresh(
            db, user_id=user_id,
            window_from=request.window_from, window_to=request.window_to,
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/crypto-ev")
async def contextual_crypto_ev(
    symbol: str | None = None,
    profile_id: UUID | None = None,
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    rows = (await db.execute(text("""
        SELECT e.*, p.name AS profile_name, pv.version_number, pv.status AS version_status
          FROM crypto_profile_ev_scores e
          JOIN profiles p ON p.id = e.profile_id
          JOIN profile_versions pv ON pv.id = e.profile_version_id
         WHERE p.user_id = :uid
           AND (:symbol IS NULL OR e.symbol = :symbol)
           AND (:profile_id IS NULL OR e.profile_id = :profile_id)
         ORDER BY e.computed_at DESC LIMIT :limit
    """), {
        "uid": str(user_id), "symbol": symbol.upper() if symbol else None,
        "profile_id": str(profile_id) if profile_id else None, "limit": limit,
    })).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}
