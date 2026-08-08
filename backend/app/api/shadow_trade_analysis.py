"""Authenticated API for durable per-trade and consolidated AI analyses."""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.ai_provider_key import AIProviderKey
from ..models.shadow_trade import ShadowTrade
from ..models.shadow_trade_analysis import ShadowTradeAnalysisJob, ShadowTradeReportRun
from ..ai_orchestration.errors import AIOrchestrationError
from ..ai_orchestration.provider_registry import default_registry
from .config import get_current_user_id


router = APIRouter(prefix="/api/shadow-trade-analysis", tags=["Shadow Trade Analysis"])


class AnalysisJobRequest(BaseModel):
    scope: Literal["TRADE", "REPORT"]
    trade_id: UUID | None = None
    report_run_id: UUID | None = None
    provider: Literal["anthropic", "openai", "gemini"]
    model: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_target(self):
        if self.scope == "TRADE" and not self.trade_id:
            raise ValueError("trade_id is required for TRADE analysis")
        if self.scope == "REPORT" and not self.report_run_id:
            raise ValueError("report_run_id is required for REPORT analysis")
        return self


@router.post("/jobs", status_code=202)
async def create_analysis_job(
    payload: AnalysisJobRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    key = (
        await db.execute(
            select(AIProviderKey).where(
                AIProviderKey.user_id == user_id,
                AIProviderKey.provider == payload.provider,
                AIProviderKey.is_active.is_(True),
                AIProviderKey.is_validated.is_(True),
            )
        )
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=409, detail="Provider precisa estar configurado e validado em Integrações de IA")
    if key.monthly_token_limit is None:
        raise HTTPException(status_code=429, detail="Defina um orçamento mensal explícito antes de usar IA")
    if int(key.tokens_used_month or 0) >= int(key.monthly_token_limit):
        raise HTTPException(status_code=429, detail="Limite mensal de tokens do provider atingido")
    try:
        resolution = default_registry().resolve(
            requested_provider=payload.provider, requested_model=payload.model,
            configured_provider=payload.provider, configured_model=payload.model,
            allow_request_override=True, required_capabilities={"text", "structured_output"},
        )
    except AIOrchestrationError as exc:
        raise HTTPException(status_code=exc.detail.http_status, detail=exc.detail.model_dump(mode="json")) from exc

    if payload.scope == "TRADE":
        target = (
            await db.execute(
                select(ShadowTrade).where(ShadowTrade.id == payload.trade_id, ShadowTrade.user_id == user_id)
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="Shadow trade not found")
        input_hash = hashlib.sha256(
            f"{target.id}:{target.updated_at}:{target.profile_config_hash}".encode()
        ).hexdigest()
    else:
        target = (
            await db.execute(
                select(ShadowTradeReportRun).where(
                    ShadowTradeReportRun.id == payload.report_run_id,
                    ShadowTradeReportRun.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="Detailed report run not found")
        input_hash = target.trade_ids_hash

    idempotency = hashlib.sha256(
        f"{user_id}:{payload.scope}:{payload.trade_id or payload.report_run_id}:{resolution.effective_provider}:{resolution.effective_model}:shadow-detailed-analysis:1.0.0:{input_hash}".encode()
    ).hexdigest()
    existing = (
        await db.execute(
            select(ShadowTradeAnalysisJob).where(
                ShadowTradeAnalysisJob.user_id == user_id,
                ShadowTradeAnalysisJob.idempotency_key == idempotency,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return _job_dict(existing)

    job = ShadowTradeAnalysisJob(
        user_id=user_id,
        tenant_id=user_id,
        scope=payload.scope,
        shadow_trade_id=payload.trade_id,
        report_run_id=payload.report_run_id,
        provider=resolution.effective_provider,
        model=resolution.effective_model,
        prompt_version="1.0.0",
        input_hash=input_hash,
        idempotency_key=idempotency,
        status="QUEUED",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    try:
        from ..tasks.shadow_trade_analysis import run

        run.apply_async(args=[str(job.id)], queue="structural_compute")
    except Exception as exc:
        job.status = "FAILED_TERMINAL"
        job.error = f"Dispatch failed: {exc}"
        job.terminal_reason = "DISPATCH_FAILED"
        job.last_error_code = "INTERNAL_ERROR"
        job.last_error_safe_message = "Falha ao enfileirar análise"
        await db.commit()
        raise HTTPException(status_code=503, detail="Falha ao enfileirar análise") from exc
    return _job_dict(job)


def _job_dict(job: ShadowTradeAnalysisJob) -> dict:
    return jsonable_encoder(
        {
            "id": job.id,
            "scope": job.scope,
            "trade_id": job.shadow_trade_id,
            "report_run_id": job.report_run_id,
            "provider": job.provider,
            "model": job.model,
            "configured_provider": job.provider,
            "configured_model": job.model,
            "effective_provider": job.provider,
            "effective_model": job.model,
            "model_resolution_reason": "validated_request_override",
            "prompt_key": "shadow-detailed-analysis",
            "prompt_version": job.prompt_version,
            "authority": "ANALYSIS_ONLY",
            "tenant_context": str(job.tenant_id or job.user_id),
            "status": job.status,
            "result": job.result_json,
            "usage": job.usage,
            "error": job.error,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "heartbeat_at": job.heartbeat_at,
            "lease_owner": job.lease_owner,
            "lease_expires_at": job.lease_expires_at,
            "attempt": job.attempt,
            "max_attempts": job.max_attempts,
            "retry_after": job.retry_after,
            "terminal_reason": job.terminal_reason,
            "last_error_code": job.last_error_code,
        }
    )


@router.get("/jobs/{job_id}")
async def get_analysis_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    job = (
        await db.execute(
            select(ShadowTradeAnalysisJob).where(
                ShadowTradeAnalysisJob.id == job_id,
                ShadowTradeAnalysisJob.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return _job_dict(job)
