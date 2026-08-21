from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_AI_REQUEST_QUESTION_CHARS = 140_000


class Authority(StrEnum):
    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    PROPOSAL_ONLY = "PROPOSAL_ONLY"
    CANDIDATE_ONLY = "CANDIDATE_ONLY"
    SHADOW_ONLY = "SHADOW_ONLY"


class AnalysisMode(StrEnum):
    LOCAL = "LOCAL"
    SYSTEMIC = "SYSTEMIC"
    REGENERATIVE = "REGENERATIVE"
    ROOT_CAUSE_AUDIT = "ROOT_CAUSE_AUDIT"


class AIRequestIntent(StrEnum):
    """Server-validated provider execution intent persisted with every request."""

    NORMAL_ANALYSIS = "NORMAL_ANALYSIS"
    FAKE_PROVIDER_CANARY = "FAKE_PROVIDER_CANARY"
    REAL_PROVIDER_CANARY = "REAL_PROVIDER_CANARY"


class ProviderModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str | None = None
    model: str | None = None
    required_capabilities: frozenset[str] = Field(default_factory=frozenset)
    allow_request_override: bool = False


class CanonicalDatasetRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    domain: Literal[
        "SHADOW_PORTFOLIO", "PROFILE_INTELLIGENCE", "CALIBRATION", "ML_BAYESIAN",
        "MARKET_REGIME", "STRATEGY_PROFILES", "SCORE_ENGINE", "GLOBAL_RISK",
        "STRATEGIES", "SOCIAL_SCORE", "INTELLIGENCE_RUNS", "AUDIT_VERSION_MEMORY",
    ]
    window_start: datetime
    window_end: datetime
    source_labels: tuple[str, ...]
    time_anchor: str
    outcome_contract: str
    event_identity_contract: str
    filters: dict[str, Any] = Field(default_factory=dict)
    exclusions: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def valid_window(self):
        if self.window_start >= self.window_end:
            raise ValueError("dataset window_start must be before window_end")
        if not self.source_labels:
            raise ValueError("at least one source label is required")
        return self


class ConfigurationScope(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile_id: UUID | None = None
    require_complete_bundle: bool = True


class EvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: str
    reference: str
    hash: str | None = None


class ToolCallSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_call_id: UUID
    tool_name: str
    side_effect: str
    status: str


class AIUsage(BaseModel):
    model_config = ConfigDict(frozen=True)
    tokens_input: int = Field(ge=0)
    tokens_output: int = Field(ge=0)
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)
    actual_cost: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = "USD"
    pricing_snapshot_version: str
    reservation: Decimal = Field(default=Decimal("0"), ge=0)
    limit: Decimal | None = Field(default=None, ge=0)
    remaining: Decimal | None = None


class AIRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    ai_request_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    requested_by_user_id: UUID | None = None
    origin_module: str = Field(min_length=1, max_length=120)
    origin_view: str | None = Field(default=None, max_length=200)
    analysis_mode: AnalysisMode
    authority: Authority
    request_intent: AIRequestIntent = AIRequestIntent.NORMAL_ANALYSIS
    provider_request: ProviderModelRequest
    prompt_key: str = Field(min_length=1, max_length=160)
    prompt_version: str | None = Field(default=None, max_length=40)
    dataset_request: CanonicalDatasetRequest
    configuration_scope: ConfigurationScope
    question: str = Field(min_length=1, max_length=MAX_AI_REQUEST_QUESTION_CHARS)
    analysis_prompt_version_id: UUID | None = None
    frozen_context: dict[str, Any] = Field(default_factory=dict)
    tool_allowlist: tuple[str, ...] = ()
    correlation_id: str = Field(min_length=1, max_length=160)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("tenant_id")
    @classmethod
    def tenant_required(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("tenant_id is required")
        return value


class AIResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    ai_request_id: UUID
    status: str
    tenant_id: UUID
    provider: str
    requested_model: str | None
    configured_model: str | None
    effective_model: str
    model_resolution_id: UUID
    prompt_version_id: UUID
    prompt_hash: str
    dataset_snapshot_id: UUID
    dataset_hash: str
    configuration_bundle_id: UUID
    configuration_bundle_hash: str
    analysis: dict[str, Any]
    recommendations: tuple[dict[str, Any], ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    tool_calls: tuple[ToolCallSummary, ...] = ()
    usage: AIUsage
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    terminal_reason: str | None = None
    completed_at: datetime | None = None
