"""HTTP contract for reviewed in-place profile optimizations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.profile import Profile
from ..models.shadow_trade import ShadowTrade
from ..models.shadow_trade_analysis import ShadowTradeAnalysisJob, ShadowTradeReportItem
from ..services.profile_optimization_service import (
    approve,
    create_dry_run,
    document_hash,
    execute,
    get_plan,
    optimization_to_dict,
    rollback,
)
from .config import get_current_user_id


router = APIRouter(prefix="/api/profile-optimizations", tags=["Profile Optimizations"])


class PatchChange(BaseModel):
    op: Literal["add", "replace", "remove"]
    path: str = Field(min_length=2, max_length=500)
    old_value: Any = None
    value: Any = None
    reason: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list)


class OptimizationPatch(BaseModel):
    schema_: Literal["scalpyn.profile_optimization_patch"] = Field(alias="schema")
    schema_version: Literal["1.0.0"]
    target: dict[str, Any]
    evidence: dict[str, Any] = Field(default_factory=dict)
    objective: str | None = Field(default=None, max_length=2000)
    risk: str | None = Field(default=None, max_length=4000)
    changes: list[PatchChange] = Field(default_factory=list, max_length=200)
    score_matrix_patch: dict[str, Any] = Field(default_factory=dict)
    score_assignment: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any]


class ConfirmationRequest(BaseModel):
    confirmation_text: str = Field(min_length=1, max_length=80)


class FromAnalysisRequest(BaseModel):
    recommendation_index: int = Field(ge=0)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Profile optimization failed: {exc}")


@router.post("/dry-run", status_code=201)
async def optimization_dry_run(
    body: OptimizationPatch,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await create_dry_run(
            db,
            user_id,
            patch=body.model_dump(by_alias=True),
            source="EXTERNAL_JSON",
        )
    except Exception as exc:
        raise _http_error(exc)


@router.post("/from-analysis/{job_id}", status_code=201)
async def optimization_from_analysis(
    job_id: UUID,
    body: FromAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        job = (
            await db.execute(
                select(ShadowTradeAnalysisJob).where(
                    ShadowTradeAnalysisJob.id == job_id,
                    ShadowTradeAnalysisJob.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if job is None:
            raise LookupError("Analysis job not found")
        if job.status != "COMPLETED" or not job.result_json:
            raise ValueError("Analysis job is not completed with structured output")
        recommendations = job.result_json.get("recommendations") or []
        if body.recommendation_index >= len(recommendations):
            raise ValueError("Recommendation index does not exist")
        recommendation = deepcopy(recommendations[body.recommendation_index])
        profile_id = UUID(str(recommendation.get("profile_id")))
        if job.scope == "TRADE":
            sample_rows = (
                await db.execute(
                    select(ShadowTrade.id, ShadowTrade.profile_id).where(
                        ShadowTrade.id == job.shadow_trade_id,
                        ShadowTrade.user_id == user_id,
                    )
                )
            ).all()
        else:
            sample_rows = (
                await db.execute(
                    select(ShadowTrade.id, ShadowTrade.profile_id)
                    .join(ShadowTradeReportItem, ShadowTradeReportItem.shadow_trade_id == ShadowTrade.id)
                    .where(ShadowTradeReportItem.report_run_id == job.report_run_id)
                )
            ).all()
        sample_trade_ids = {str(row.id) for row in sample_rows}
        sample_profile_ids = {str(row.profile_id) for row in sample_rows if row.profile_id is not None}
        evidence_trade_ids = {str(value) for value in recommendation.get("evidence_trade_ids") or []}
        if not evidence_trade_ids:
            raise ValueError("AI recommendation requires evidence_trade_ids from the analyzed sample")
        if not evidence_trade_ids.issubset(sample_trade_ids):
            raise ValueError("AI recommendation references trades outside the analyzed sample")
        if str(profile_id) not in sample_profile_ids:
            raise ValueError("AI recommendation references a profile outside the analyzed sample")
        for change in recommendation.get("changes") or []:
            refs = {str(value) for value in change.get("evidence_refs") or []}
            if not refs or not refs.issubset(sample_trade_ids):
                raise ValueError("Every AI profile change requires valid evidence_refs from the analyzed sample")
        profile = (
            await db.execute(
                select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise LookupError("Recommended profile not found")
        patch = {
            "schema": "scalpyn.profile_optimization_patch",
            "schema_version": "1.0.0",
            "target": {
                "profile_id": str(profile.id),
                "profile_name": profile.name,
                "expected_profile_config_hash": document_hash(profile.config or {}),
                "expected_profile_version": profile.profile_version.isoformat() if profile.profile_version else None,
            },
            "evidence": {
                "analysis_job_id": str(job.id),
                "report_run_id": str(job.report_run_id) if job.report_run_id else None,
                "evidence_trade_ids": recommendation.get("evidence_trade_ids") or [],
            },
            "objective": recommendation.get("rationale") or f"Optimize {profile.name}",
            "changes": recommendation.get("changes") or [],
            "score_matrix_patch": recommendation.get("score_matrix_patch") or {},
            "score_assignment": recommendation.get("score_assignment") or {},
            "constraints": {
                "preserve_profile_id": True,
                "preserve_profile_name": True,
                "preserve_profile_version": False,
                "create_profile": False,
            },
        }
        return await create_dry_run(db, user_id, patch=patch, source="AI_ANALYSIS", source_id=str(job.id))
    except Exception as exc:
        raise _http_error(exc)


@router.get("/{plan_id}")
async def get_optimization(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return optimization_to_dict(await get_plan(db, user_id, plan_id))
    except Exception as exc:
        raise _http_error(exc)


@router.post("/{plan_id}/approve")
async def approve_optimization(
    plan_id: UUID,
    body: ConfirmationRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await approve(db, user_id, plan_id, body.confirmation_text)
    except Exception as exc:
        raise _http_error(exc)


@router.post("/{plan_id}/execute")
async def execute_optimization(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await execute(db, user_id, plan_id)
    except Exception as exc:
        raise _http_error(exc)


@router.post("/{plan_id}/rollback")
async def rollback_optimization(
    plan_id: UUID,
    body: ConfirmationRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await rollback(db, user_id, plan_id, body.confirmation_text)
    except Exception as exc:
        raise _http_error(exc)
