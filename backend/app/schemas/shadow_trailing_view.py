"""Read-only presentation contract; percentages are gross, relative to entry."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class TrailingLevel(BaseModel):
    price: float | None = None
    pct: float | None = None
    at: datetime | None = None


class TrailingRegime(BaseModel):
    label: Literal['Bullish', 'Bearish', 'Neutral'] | None = None
    original: str | None = None
    quality: str = 'UNAVAILABLE'
    source: str | None = None
    timeframe: str | None = None
    at: datetime | None = None


class ShadowTrailingView(BaseModel):
    state: str
    mode: str
    origin: str | None = None
    entry: TrailingLevel = Field(default_factory=TrailingLevel)
    activation: TrailingLevel = Field(default_factory=TrailingLevel)
    observed: TrailingLevel = Field(default_factory=TrailingLevel)
    maximum: TrailingLevel = Field(default_factory=TrailingLevel)
    floor: TrailingLevel = Field(default_factory=TrailingLevel)
    pending_floor: TrailingLevel = Field(default_factory=TrailingLevel)
    trigger: TrailingLevel = Field(default_factory=TrailingLevel)
    next_step: TrailingLevel = Field(default_factory=TrailingLevel)
    next_step_floor: TrailingLevel = Field(default_factory=TrailingLevel)
    remaining_pp: float | None = None
    distance_to_floor_pp: float | None = None
    pending_reason: str | None = None
    quality: str = 'UNAVAILABLE'
    last_evaluated_at: datetime | None = None
    policy_version: str | None = None
    asset_regime: TrailingRegime = Field(default_factory=TrailingRegime)
    market_regime: TrailingRegime = Field(default_factory=TrailingRegime)
