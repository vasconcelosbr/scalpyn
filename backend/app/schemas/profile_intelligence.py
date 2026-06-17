"""Pydantic schemas for Profile Intelligence Engine API."""
from __future__ import annotations
from datetime import datetime
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
    enable_anthropic_explanations: Optional[bool] = None
    enable_optuna: Optional[bool] = None
    enable_association_rules: Optional[bool] = None
    enable_dynamic_combinations: Optional[bool] = None
    enable_lightgbm: Optional[bool] = None
    enable_catboost: Optional[bool] = None


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
