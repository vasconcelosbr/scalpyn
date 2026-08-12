from __future__ import annotations

from enum import StrEnum
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalysisChatRequestKind(StrEnum):
    PRIMARY_ANALYSIS = "PRIMARY_ANALYSIS"
    FOLLOW_UP_CHAT = "FOLLOW_UP_CHAT"
    CHILD_ANALYSIS = "CHILD_ANALYSIS"
    PROPOSAL_DRAFT = "PROPOSAL_DRAFT"
    CONVERSATION_SUMMARY = "CONVERSATION_SUMMARY"


class AnalysisChatDataMode(StrEnum):
    FROZEN_ANALYSIS_ONLY = "FROZEN_ANALYSIS_ONLY"
    ALLOW_READONLY_REFRESH = "ALLOW_READONLY_REFRESH"
    CREATE_CHILD_ANALYSIS = "CREATE_CHILD_ANALYSIS"
    DRAFT_PROPOSAL = "DRAFT_PROPOSAL"


class AnalysisChatRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    readonly_refresh_enabled: bool = False
    child_analysis_enabled: bool = False
    proposals_enabled: bool = False
    governed_actions_enabled: bool = False
    live_config_write_enabled: bool = False
    streaming_enabled: bool = False
    summary_enabled: bool = False
    summary_message_threshold: int = Field(default=10, ge=4, le=100)
    recent_message_limit: int = Field(default=10, ge=4, le=20)
    max_message_characters: int = Field(default=4000, ge=256, le=12000)
    provider_max_cost_usd: Decimal = Field(default=Decimal("0"), ge=0, le=10)
    request_token_limit: int = Field(default=0, ge=0)
    daily_token_limit: int = Field(default=0, ge=0)
    monthly_token_limit: int = Field(default=0, ge=0)


class AnalysisChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    answer_type: str
    based_on: str
    parent_analysis_run_id: UUID
    modules_consulted: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    new_data_queried: bool = False
    new_data_window: dict[str, Any] | None = None
    child_analysis_run_id: UUID | None = None
    proposal: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
