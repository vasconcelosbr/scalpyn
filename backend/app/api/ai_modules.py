from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_orchestration.contracts import AnalysisMode, Authority
from ..ai_orchestration.hashing import canonical_hash
from ..ai_orchestration.langgraph.config import get_langgraph_settings
from ..ai_orchestration.provider_registry import default_registry
from ..database import get_db
from ..models.systemic_ai import AIBudgetPolicyRecord, AIModelApprovalRecord
from ..services.module_ai_analysis_service import ModuleAIAnalysisService
from .config import get_current_user_id


router = APIRouter(prefix="/api/ai/modules", tags=["AI Modules"])

MODEL_APPROVAL_PHRASE = "APROVO MODELO E CUSTO"


class CreateModelApprovalRequest(BaseModel):
    provider: Literal["anthropic", "openai", "gemini"]
    model: str = Field(min_length=1, max_length=200)
    max_cost_usd: Decimal = Field(gt=0)
    input_cost_per_million: Decimal = Field(ge=0)
    output_cost_per_million: Decimal = Field(ge=0)
    pricing_source_url: AnyHttpUrl
    pricing_observed_at: datetime
    approval_phrase: str
    scope: Literal["SYSTEMIC_MODULE_ANALYSIS"] = "SYSTEMIC_MODULE_ANALYSIS"
    module: Literal[
        "strategy_profiles", "ml_models", "shadow_portfolio", "score_engine",
        "global_risk", "strategies", "social_score",
    ]
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    request_token_limit: int = Field(gt=0)
    daily_token_limit: int = Field(gt=0)
    monthly_token_limit: int = Field(gt=0)

    @field_validator("pricing_observed_at")
    @classmethod
    def pricing_timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("pricing_observed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def budget_is_bounded(self):
        reservation = self.max_input_tokens + self.max_output_tokens
        if self.request_token_limit < reservation:
            raise ValueError("request_token_limit must cover max_input_tokens + max_output_tokens")
        if self.daily_token_limit < self.request_token_limit:
            raise ValueError("daily_token_limit must be at least request_token_limit")
        if self.monthly_token_limit < self.daily_token_limit:
            raise ValueError("monthly_token_limit must be at least daily_token_limit")
        return self


@router.post("/model-approvals", status_code=201)
async def create_model_approval(
    payload: CreateModelApprovalRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    if payload.approval_phrase.strip() != MODEL_APPROVAL_PHRASE:
        raise HTTPException(status_code=400, detail={"code": "MODEL_COST_APPROVAL_PHRASE_REQUIRED"})
    now = datetime.now(timezone.utc)
    catalog = default_registry()
    catalog_entry = catalog.get_entry(payload.provider, payload.model)
    if payload.max_input_tokens > catalog_entry.max_input:
        raise HTTPException(status_code=422, detail={"code": "MODEL_INPUT_CAP_EXCEEDED"})
    if payload.max_output_tokens > catalog_entry.max_output:
        raise HTTPException(status_code=422, detail={"code": "MODEL_OUTPUT_CAP_EXCEEDED"})
    million = Decimal("1000000")
    worst_case_cost = (
        Decimal(payload.max_input_tokens) * payload.input_cost_per_million
        + Decimal(payload.max_output_tokens) * payload.output_cost_per_million
    ) / million
    if payload.max_cost_usd < worst_case_cost:
        raise HTTPException(status_code=422, detail={"code": "MODEL_COST_CAP_BELOW_WORST_CASE"})
    pricing_payload = {
        "provider": payload.provider, "model": payload.model,
        "input_cost_per_million": str(payload.input_cost_per_million),
        "output_cost_per_million": str(payload.output_cost_per_million),
        "pricing_source_url": str(payload.pricing_source_url),
        "pricing_observed_at": payload.pricing_observed_at.isoformat(),
        "max_input_tokens": payload.max_input_tokens,
        "max_output_tokens": payload.max_output_tokens,
        "catalog_snapshot_hash": catalog.catalog_snapshot_hash,
    }
    pricing_snapshot_hash = canonical_hash(pricing_payload)
    record_id = uuid4()
    approval_payload = {
        "id": str(record_id), "tenant_id": str(user_id), "provider": payload.provider,
        "model": payload.model, "max_cost_usd": str(payload.max_cost_usd),
        "scope": payload.scope, "approved_by": str(user_id), "approved_at": now.isoformat(),
        "pricing_snapshot_hash": pricing_snapshot_hash,
        "module": payload.module,
        "request_token_limit": payload.request_token_limit,
        "daily_token_limit": payload.daily_token_limit,
        "monthly_token_limit": payload.monthly_token_limit,
    }
    record = AIModelApprovalRecord(
        id=record_id, tenant_id=user_id, provider=payload.provider, model=payload.model,
        max_cost_usd=payload.max_cost_usd,
        input_cost_per_million=payload.input_cost_per_million,
        output_cost_per_million=payload.output_cost_per_million,
        max_output_tokens=payload.max_output_tokens,
        pricing_source_url=str(payload.pricing_source_url),
        pricing_observed_at=payload.pricing_observed_at,
        pricing_snapshot_hash=pricing_snapshot_hash,
        approval_phrase_hash=canonical_hash(payload.approval_phrase.strip()),
        scope=payload.scope, status="APPROVED", approved_by=user_id,
        approved_at=now,
        expires_at=now + timedelta(seconds=get_langgraph_settings().model_approval_ttl_seconds),
        content_hash=canonical_hash(approval_payload),
    )
    budget = (await db.execute(select(AIBudgetPolicyRecord).where(
        AIBudgetPolicyRecord.tenant_id == user_id,
        AIBudgetPolicyRecord.provider == payload.provider,
        AIBudgetPolicyRecord.model == payload.model,
        AIBudgetPolicyRecord.module == payload.module,
    ))).scalar_one_or_none()
    if budget is None:
        budget = AIBudgetPolicyRecord(
            tenant_id=user_id,
            provider=payload.provider,
            model=payload.model,
            module=payload.module,
        )
        db.add(budget)
    budget.request_token_limit = payload.request_token_limit
    budget.daily_token_limit = payload.daily_token_limit
    budget.monthly_token_limit = payload.monthly_token_limit
    budget.null_limit_policy = "DENY"
    budget.is_active = True
    db.add(record)
    await db.commit()
    return {
        "id": str(record.id), "provider": record.provider, "model": record.model,
        "max_cost_usd": str(record.max_cost_usd), "scope": record.scope,
        "pricing_snapshot_hash": record.pricing_snapshot_hash,
        "expires_at": record.expires_at.isoformat(), "content_hash": record.content_hash,
        "max_input_tokens": payload.max_input_tokens,
        "max_output_tokens": record.max_output_tokens,
        "worst_case_cost_usd": str(worst_case_cost),
        "budget": {
            "module": budget.module,
            "request_token_limit": budget.request_token_limit,
            "daily_token_limit": budget.daily_token_limit,
            "monthly_token_limit": budget.monthly_token_limit,
            "null_limit_policy": budget.null_limit_policy,
            "is_active": budget.is_active,
            "expires_at": record.expires_at.isoformat(),
        },
    }


class CreateModuleAnalysisRequest(BaseModel):
    origin_module: Literal[
        "strategy_profiles", "ml_models", "shadow_portfolio", "score_engine",
        "global_risk", "strategies", "social_score",
    ]
    origin_view: str = Field(min_length=1, max_length=200)
    entity_ids: tuple[str, ...] = ()
    filters: dict[str, Any] = Field(default_factory=dict)
    analysis_mode: AnalysisMode = AnalysisMode.SYSTEMIC
    question: str = Field(min_length=1, max_length=20_000)
    authority: Authority = Authority.ANALYSIS_ONLY
    provider: Literal["anthropic", "openai", "gemini"]
    model: str = Field(min_length=1, max_length=200)
    model_approval_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=160)


@router.post("/analysis-runs", status_code=202)
async def create_module_analysis_run(
    payload: CreateModuleAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        run = await ModuleAIAnalysisService.create_run(
            db, tenant_id=user_id, user_id=user_id,
            origin_module=payload.origin_module, origin_view=payload.origin_view,
            entity_ids=payload.entity_ids, filters=payload.filters,
            analysis_mode=payload.analysis_mode, question=payload.question,
            authority=payload.authority, provider=payload.provider, model=payload.model,
            model_approval_id=payload.model_approval_id,
            idempotency_key=payload.idempotency_key,
        )
        await db.commit()
        await db.refresh(run)
    except RuntimeError as exc:
        code = str(exc).split(":", 1)[0]
        status = 403 if code.endswith(("DENIED", "READ_ONLY")) else 409
        raise HTTPException(status_code=status, detail={"code": code}) from exc
    from ..tasks.ai_orchestration import start_graph_run
    start_graph_run.apply_async(args=[str(run.id)], queue="ai_orchestration")
    return {
        "id": str(run.id), "ai_request_id": str(run.ai_request_id),
        "status": run.status, "authority": run.authority,
        "graph_definition_id": str(run.graph_definition_id),
    }
