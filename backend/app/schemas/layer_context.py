"""Versioned observational context emitted by the prepared R6 L1 layer."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

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
