from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ChatContext(BaseModel):
    screen: str = "profile_intelligence_copilot"
    run_id: str | None = None
    selected_profile_id: str | None = None
    lookback_days: int = Field(default=7, ge=1, le=365)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12_000)
    session_id: UUID | None = None
    context: ChatContext = Field(default_factory=ChatContext)
    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str | None = Field(default=None, max_length=120)


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="Consulta solicitada pelo usuário", max_length=1000)
    session_id: UUID | None = None


class PatternRequest(BaseModel):
    analysis: Literal["profile_performance", "indicator_performance", "period_comparison"]
    lookback_days: int = Field(default=7, ge=1, le=365)
    min_sample: int = Field(default=30, ge=1, le=100_000)
    session_id: UUID | None = None


class ChangeItem(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    old_value: Any = None
    new_value: Any
    reason: str = Field(min_length=1, max_length=2000)


class DryRunRequest(BaseModel):
    action_type: Literal["UPDATE_PROFILE_CONFIG"] = "UPDATE_PROFILE_CONFIG"
    profile_id: UUID
    objective: str = Field(min_length=1, max_length=2000)
    evidence: dict[str, Any] = Field(default_factory=dict)
    changes: list[ChangeItem] = Field(min_length=1, max_length=100)
    risk: str = Field(min_length=1, max_length=4000)
    session_id: UUID | None = None


class ApprovalRequest(BaseModel):
    confirmation_text: str = Field(min_length=1, max_length=80)


class SkillCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    skill_type: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=50_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: str | None = Field(default=None, max_length=160)


class SkillUpdateRequest(BaseModel):
    status: Literal["ACTIVE", "INACTIVE", "PENDING_APPROVAL", "REJECTED"] | None = None
    content: str | None = Field(default=None, min_length=1, max_length=50_000)
    metadata: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
