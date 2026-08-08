from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AIMetricName(StrEnum):
    JOBS_QUEUED = "ai.jobs.queued"
    JOBS_RUNNING = "ai.jobs.running"
    JOBS_STALE = "ai.jobs.stale"
    JOBS_RECOVERED = "ai.jobs.recovered"
    PROVIDER_OUTCOME = "ai.provider.outcome"
    MODEL_RESOLUTION_CONFLICT = "ai.model_resolution.conflict"
    TENANT_SCOPE_DENIAL = "ai.tenant_scope.denial"
    BUDGET_DENIAL = "ai.budget.denial"
    PROMPT_SCHEMA_FAILURE = "ai.prompt.schema_failure"
    DATASET_QUALITY_BLOCK = "ai.dataset.quality_block"
    BUNDLE_MISSING = "ai.bundle.missing"
    TOOL_DENIAL = "ai.tool.denial"
    USAGE = "ai.usage"
    INVARIANT_CONFLICT = "ai.invariant.conflict"


class AITraceContext(BaseModel):
    """Secret-free correlation fields required on orchestration telemetry."""

    model_config = ConfigDict(frozen=True)
    ai_request_id: UUID
    correlation_id: str
    tenant_id: UUID
    job_id: UUID | None = None
    attempt: int = Field(default=1, ge=1)
    model_resolution_id: UUID | None = None
    prompt_version_id: UUID | None = None
    dataset_snapshot_id: UUID | None = None
    configuration_bundle_id: UUID | None = None
    tool_call_id: UUID | None = None
    usage_id: UUID | None = None
    terminal_reason: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)


ALERTABLE_CONDITIONS = frozenset({
    "STALE_LEASE",
    "PROVIDER_CREDIT_EXHAUSTED",
    "PROVIDER_AUTH_ERROR",
    "MODEL_UNKNOWN",
    "PROVIDER_BAD_REQUEST_REPEATED",
    "CIRCUIT_BREAKER_OPEN",
    "CROSS_TENANT_VIOLATION",
    "INVARIANT_CONFLICT",
})
