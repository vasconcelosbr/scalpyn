"""
config_schemas.py
-----------------
Pydantic schemas for config_types stored in config_profiles.config_json.

Architecture — 3 execution layers (zero hardcode):
  LAYER 1 · filters  → binary pre-filter  (before score)
  LAYER 2 · score    → 0-100 scoring       (ranking opportunities)
  LAYER 3 · blocks   → absolute veto       (post-score, capital protection)

Signal was removed as a separate entity.
What were Signal conditions become Block entry_triggers.
"""

from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, validator


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1 — FILTERS   config_type = 'filters'
# ─────────────────────────────────────────────────────────────────────────────

class FilterRule(BaseModel):
    id: str
    name: str
    enabled: bool = True
    indicator: str
    operator: Literal["<", "<=", ">", ">=", "=", "!=", "between"]
    value: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    description: Optional[str] = None

    @validator("min", "max", always=True)
    def validate_between(cls, v, values):
        if values.get("operator") == "between" and v is None:
            raise ValueError("min and max are required for operator='between'")
        return v


class FiltersConfig(BaseModel):
    """config_type = 'filters'"""
    enabled: bool = True
    logic: Literal["AND", "OR"] = "AND"
    filters: List[FilterRule] = Field(default_factory=lambda: [
        FilterRule(id="f_min_volume", name="Minimum 24h Volume",     indicator="volume_24h",   operator=">=", value=1_000_000),
        FilterRule(id="f_min_adx",    name="Minimum Trend Strength", indicator="adx",          operator=">=", value=20),
        FilterRule(id="f_max_spread", name="Maximum Spread %",       indicator="spread_pct",   operator="<=", value=0.5),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2 — SCORE   config_type = 'score'
# ─────────────────────────────────────────────────────────────────────────────

class ScoreThresholds(BaseModel):
    strong_buy: float = Field(80, ge=0, le=100)
    buy:        float = Field(65, ge=0, le=100)
    neutral:    float = Field(40, ge=0, le=100)


class ScoreConfig(BaseModel):
    """config_type = 'score'"""
    thresholds: ScoreThresholds = ScoreThresholds()
    auto_select_top_n: int = Field(5, ge=1, le=50)
    auto_select_min_score: float = Field(80, ge=0, le=100)


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3 — BLOCKS   config_type = 'block'
# ─────────────────────────────────────────────────────────────────────────────

class HardBlock(BaseModel):
    id: str
    name: str
    enabled: bool = True
    category: Literal["hard_block", "entry_trigger"] = "hard_block"
    indicator: str
    type: Literal["threshold", "range", "condition", "crossing"]
    operator: Optional[str] = None
    value: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    condition: Optional[str] = None
    direction: Optional[Literal["up", "down"]] = None
    description: Optional[str] = None
    severity: Literal["hard", "soft"] = "hard"


class BlocksConfig(BaseModel):
    """config_type = 'block'"""
    blocks: List[HardBlock] = Field(default_factory=list)
    entry_triggers: List[dict] = Field(default_factory=list)
    entry_logic: Literal["AND", "OR"] = "AND"
