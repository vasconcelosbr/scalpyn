"""Public, allowlisted machine export of the displayed strong-long arrows."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, FiniteFloat


class StrongDirection(BaseModel):
    direction: Literal["long"]
    strength: Literal["forte"]
    reason: str


class StrongAsset(BaseModel):
    symbol: str
    direction: StrongDirection
    pump_monitor_score: FiniteFloat | None


class PumpStrongResponse(BaseModel):
    pool_id: UUID
    generated_at: datetime
    data_age_seconds: float
    stale: bool
    config_version: int
    config_hash: str
    total_assets: int
    matched_assets: int
    rows: list[StrongAsset]
    notice: str = "OBSERVAÇÃO - não é sinal de entrada"
