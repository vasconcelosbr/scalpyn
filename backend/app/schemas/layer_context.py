"""Versioned observational context emitted by the prepared R6 L1 layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


LayerVerdict = Literal["PASS", "REJECT", "INSUFFICIENT_DATA", "UNAVAILABLE"]


class LayerVerdictRecord(BaseModel):
    verdict: LayerVerdict
    rule: Optional[str] = None
    computed_at: Optional[datetime] = None
    contract_version: str


class L1DecisionContextV1(BaseModel):
    """Axes stay independent so downstream layers never invent precedence."""

    l1_trend_state: Literal["TREND_STRONG", "TREND_WEAK", "RANGING"]
    l1_volatility_state: Literal["HIGH", "NORMAL", "LOW"]
    l1_structure_event: Literal["BREAKOUT", "PULLBACK", "NONE"]
    l1_direction: Literal["UP", "DOWN", "NEUTRAL"]
    l1_strength: float = Field(ge=0, le=1)
    l1_regime_since: datetime
    l1_previous_regime: Optional[str] = None
    l1_timeframe: Literal["1h"]
    l1_computed_at: datetime
    l1_candle_policy: Literal["CLOSED_ONLY"]
    l1_source: str = Field(min_length=1)
    l1_contract_version: Literal["multilayer_decision_context_v1"]
    l1_verdict: LayerVerdict
    l1_verdict_reason: Optional[str] = None


class ProfileIdentity(BaseModel):
    profile_id: str = Field(min_length=1)
    profile_version_id: str = Field(min_length=1)
    profile_config_hash: str = Field(min_length=64, max_length=64)


class CandleIdentity(BaseModel):
    symbol: str = Field(min_length=1)
    market_type: Literal["spot"]
    timeframe: Literal["1h", "15m", "5m"]
    source_timestamp: datetime
    closed: Literal[True]
    source_provider: str = Field(min_length=1)
    provider_policy_id: str = Field(min_length=1)


class L1DecisionContextV2(BaseModel):
    contract_version: Literal["l1_decision_context_v2"] = "l1_decision_context_v2"
    direction: Literal["UP", "DOWN", "NEUTRAL"]
    strength: float = Field(ge=0, le=1)
    regime: Literal["TREND", "RANGE", "TRANSITION", "UNAVAILABLE"]
    volatility: Literal["HIGH", "NORMAL", "LOW", "UNAVAILABLE"]
    structure: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNAVAILABLE"]
    validity: Literal["VALID", "UNAVAILABLE", "EXPIRED", "INVALID"]
    verdict: LayerVerdict
    reason_codes: list[str] = Field(default_factory=list)
    candle: CandleIdentity
    profile: ProfileIdentity
    computed_at: datetime
    expires_at: datetime
    indicators_hash: str = Field(min_length=64, max_length=64)
    context_hash: Optional[str] = None


class L2DecisionContextV1(BaseModel):
    contract_version: Literal["l2_decision_context_v1"] = "l2_decision_context_v1"
    local_direction: Literal["UP", "DOWN", "NEUTRAL"]
    setup_state: Literal[
        "PULLBACK_RECLAIM", "BREAKOUT_RETEST", "INVALIDATED", "NONE"
    ]
    extension_atr: Optional[float] = None
    support: Optional[float] = None
    resistance: Optional[float] = None
    invalidation: Optional[float] = None
    validity: Literal["VALID", "UNAVAILABLE", "EXPIRED", "INVALID"]
    verdict: LayerVerdict
    reason_codes: list[str] = Field(default_factory=list)
    candle: CandleIdentity
    profile: ProfileIdentity
    l1_context_hash: str = Field(min_length=64, max_length=64)
    computed_at: datetime
    expires_at: datetime
    indicators_hash: str = Field(min_length=64, max_length=64)
    context_hash: Optional[str] = None


class MultilayerDecisionContextV2(BaseModel):
    contract_version: Literal["multilayer_decision_context_v2"] = (
        "multilayer_decision_context_v2"
    )
    mode: Literal["SHADOW"] = "SHADOW"
    operational_effect: Literal[False] = False
    l1_snapshot: Dict[str, Any]
    l1_context_hash: str = Field(min_length=64, max_length=64)
    l2_snapshot: Dict[str, Any]
    l2_context_hash: str = Field(min_length=64, max_length=64)
    l3_confirmation: Dict[str, Any]
    canonical_score: Optional[float] = None
    verdicts: Dict[str, LayerVerdictRecord]
    observational_decision: Literal["PASS", "WAIT", "REJECT"]
    computed_at: datetime
    context_hash: Optional[str] = None
