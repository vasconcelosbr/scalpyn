"""Pydantic schemas for Profile Intelligence Engine API."""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    lookback_days: int = Field(default=60, ge=7, le=365)
    min_closed_trades: int = Field(default=30, ge=10, le=500)
    include_counterfactual: bool = True
    include_dynamic_combinations: bool = True
    include_association_rules: bool = False
    include_optuna: bool = False
    include_ai_explanation: bool = False
    profiles_filter: Optional[List[str]] = None
    max_combinations: int = Field(default=500, ge=10, le=5000)
    settings_override: Optional[Dict[str, Any]] = None


class RunResponse(BaseModel):
    run_id: str
    status: str
    message: str


class PISettingsUpdate(BaseModel):
    min_support: Optional[float] = None
    min_closed_trades: Optional[int] = None
    min_lift: Optional[float] = None
    min_win_rate: Optional[float] = None
    max_avg_mae: Optional[float] = None
    max_avg_holding_seconds: Optional[float] = None
    required_tp_30m_rate: Optional[float] = None
    max_combinations_per_run: Optional[int] = None
    analysis_sources: Optional[List[str]] = None
    indicator_winning_lift: Optional[float] = None
    indicator_losing_winrate_ratio: Optional[float] = None
    validation_min_discovery_trades: Optional[int] = None
    validation_min_trades: Optional[int] = None
    validation_min_lift: Optional[float] = None
    validation_min_winrate_delta: Optional[float] = None
    validation_max_single_symbol_share: Optional[float] = None
    validation_max_single_day_share: Optional[float] = None
    validation_min_distinct_symbols: Optional[int] = None
    validation_min_distinct_days: Optional[int] = None
    validation_min_assoc_support: Optional[float] = None
    validation_min_assoc_confidence: Optional[float] = None
    validation_min_lift_retention: Optional[float] = None
    adjustment_min_profile_trades: Optional[int] = None
    adjustment_max_win_rate: Optional[float] = None
    adjustment_score_bump: Optional[int] = None
    adjustment_score_cap: Optional[int] = None
    enable_anthropic_explanations: Optional[bool] = None
    enable_optuna: Optional[bool] = None
    enable_association_rules: Optional[bool] = None
    enable_dynamic_combinations: Optional[bool] = None
    enable_lightgbm: Optional[bool] = Field(
        default=None,
        description="Reserved compatibility flag; LightGBM is not implemented and is normalized to false.",
    )
    enable_catboost: Optional[bool] = Field(
        default=None,
        description="Reserved compatibility flag; CatBoost is not implemented and is normalized to false.",
    )


class CreateProfileRequest(BaseModel):
    profile_name: Optional[str] = None
    profile_description: Optional[str] = None
    mode: str = "SHADOW_ONLY"
    confirm_low_confidence: bool = False
    confirm_overfit_risk: bool = False
    create_missing_master_rules: bool = True
    reuse_existing_master_rules: bool = True
    assign_to_watchlist_id: Optional[str] = None
    dry_run: bool = False


class IndicatorShadowAdjustmentRequest(BaseModel):
    profile_ids: List[UUID] = Field(min_length=1, max_length=20)


class ManualAdjustmentCreateRequest(BaseModel):
    profile_id: UUID
    action_type: str = Field(min_length=3, max_length=50)
    target_path: Optional[str] = Field(default=None, max_length=500)
    current_value: Any = None
    proposed_value: Any = None
    run_id: Optional[UUID] = None
    indicator_stat_id: Optional[UUID] = None
    evidence_json: Dict[str, Any] = Field(default_factory=dict)
    statistical_warnings: List[Dict[str, Any]] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=12, max_length=160)


class ManualAdjustmentUpdateRequest(BaseModel):
    action_type: Optional[str] = Field(default=None, min_length=3, max_length=50)
    target_path: Optional[str] = Field(default=None, max_length=500)
    current_value: Any = None
    proposed_value: Any = None
    evidence_json: Optional[Dict[str, Any]] = None
    statistical_warnings: Optional[List[Dict[str, Any]]] = None


class ManualAdjustmentApprovalRequest(BaseModel):
    preview_hash: str = Field(min_length=64, max_length=64)
    justification: str = Field(min_length=10, max_length=4000)
    confirm_risk: bool


class ManualAdjustmentRollbackRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=4000)


class ScoreThresholdSimulationRequest(BaseModel):
    score: str
    threshold: float
    lookback_days: int = Field(default=30, ge=7, le=365)
    source: Optional[str] = None
    profile_id: Optional[UUID] = None
    profile_version_id: Optional[UUID] = None
    score_engine_version_id: Optional[UUID] = None
    timeframe: Optional[str] = Field(default=None, max_length=16)


class AutopilotSettingsUpdate(BaseModel):
    enabled: bool
    settings: Optional[Dict[str, Any]] = None


class CandidateApprovalRequest(BaseModel):
    approved_by: UUID
    approval_reason: str = Field(min_length=10, max_length=2000)
    confirm_risk: bool
    approval_source: str = Field(default="profile_intelligence_ui", min_length=3, max_length=80)


class CandidateRejectionRequest(BaseModel):
    rejection_reason: str = Field(min_length=5, max_length=2000)
