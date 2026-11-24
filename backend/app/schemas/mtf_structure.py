"""Additive read-only presentation contract; not a strategy configuration."""
from typing import Literal
from .mtf_rules import Timeframe
from pydantic import BaseModel, FiniteFloat


class StructurePivot(BaseModel):
    time: float
    confirmed_at: float
    price: FiniteFloat
    label: str
    kind: str


class StructureFrame(BaseModel):
    trend: Literal['UP', 'DOWN', 'RANGE'] | None = None
    pivots: list[StructurePivot] = []
    last_hl: StructurePivot | None = None
    last_lh: StructurePivot | None = None
    last_pivot_age_candles: int | None = None
    atr: FiniteFloat | None = None
    ema50: FiniteFloat | None = None
    closed_close: FiniteFloat | None = None
    closed_at: str | None = None
    extension_atr: FiniteFloat | None = None
    live_candle: dict | None = None
    live_change_pct: FiniteFloat | None = None


class StructureConflict(BaseModel):
    kind: Literal['structure', 'flow']
    timeframe: Timeframe
    state: str
    sequence: list[str] = []
    ratio: FiniteFloat | None = None


class StructureContext(BaseModel):
    stage_timeframes: dict[str, Timeframe] | None = None
    ema_period: int = 50
    source: Literal['chart', 'observation']
    as_of: str
    evaluated_at: str
    valid_until: str
    config_hash: str
    profile_id: str | None = None
    frames: dict[str, StructureFrame]
    current_price: FiniteFloat | None = None
    current_price_source: str | None = None
    invalidation: StructurePivot | None = None
    invalidation_distance_atr: FiniteFloat | None = None
    conflict: bool | None = None
    conflicts: list[StructureConflict] = []
    operational_effect: Literal[False] = False
