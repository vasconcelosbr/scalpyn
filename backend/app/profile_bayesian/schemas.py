"""Typed public contracts for Profile Bayesian Intelligence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AnalysisStatus(StrEnum):
    PENDING = "PENDING"
    BUILDING_DATASET = "BUILDING_DATASET"
    VALIDATING_DATA = "VALIDATING_DATA"
    SAMPLING = "SAMPLING"
    RUNNING_DIAGNOSTICS = "RUNNING_DIAGNOSTICS"
    ANALYZING_POSTERIOR = "ANALYZING_POSTERIOR"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DiagnosticStatus(StrEnum):
    VALID = "VALID"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_CONVERGED = "NOT_CONVERGED"
    FAILED = "FAILED"


class EvidenceGrade(StrEnum):
    INSUFFICIENT = "INSUFFICIENT"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    VERY_STRONG = "VERY_STRONG"


class AnalyzeRequest(BaseModel):
    window_from: datetime
    window_to: datetime
    profile_version_id: UUID | None = None
    policy_key: str | None = None
    indicator_names: list[str] | None = None
    random_seed: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=180)

    @model_validator(mode="after")
    def validate_window(self) -> "AnalyzeRequest":
        if self.window_to <= self.window_from:
            raise ValueError("window_to must be after window_from")
        return self


class OptimizationRequest(BaseModel):
    analysis_run_id: UUID
    random_seed: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=180)


class IndicatorEffect(BaseModel):
    profile_id: UUID
    indicator: str
    regime: str | None
    effect_direction: str
    estimated_tp_lift: float | None
    estimated_pnl_lift: float | None
    probability_positive_effect: float | None
    credible_interval_95: tuple[float | None, float | None]
    direct_sample_size: int
    shared_sample_size: int
    effective_sample_size: float | None
    evidence_grade: EvidenceGrade
    diagnostic_status: DiagnosticStatus
    recommendation: str
    details: dict[str, Any] = Field(default_factory=dict)


class CandidateChange(BaseModel):
    target_path: str
    current_value: Any
    candidate_value: Any
    absolute_delta: float | None = None
    relative_delta: float | None = None
    justification: str
    source_analysis_run_id: UUID


class CreateCandidateRequest(BaseModel):
    analysis_run_id: UUID
    optimization_study_id: UUID | None = None
    base_profile_version_id: UUID | None = None
    changes: list[CandidateChange]
    evidence: dict[str, Any]
    idempotency_key: str = Field(min_length=8, max_length=180)


class SubmitCandidateRequest(BaseModel):
    expected_status: str
    idempotency_key: str = Field(min_length=8, max_length=180)
