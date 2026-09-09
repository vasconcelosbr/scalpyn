from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PumpRadarConfig(BaseModel):
    """Validated, observation-only configuration persisted as pump_radar_v1."""

    schema_version: Literal["pump_radar_v1"] = "pump_radar_v1"
    market: Literal["gate_spot"] = "gate_spot"
    detector_timeframe: Literal["5m"] = "5m"
    capture_timeframes: list[Literal["5m", "15m", "1h"]] = Field(
        default_factory=lambda: ["5m", "15m", "1h"]
    )
    minimum_rise_pct: float = Field(default=5.0, gt=0, le=100)
    maximum_window_minutes: int = Field(default=30, ge=5, le=180)
    reference_price: Literal["base_candle_open"] = "base_candle_open"
    base_selection: Literal["lowest_open_earliest_tie"] = "lowest_open_earliest_tie"
    confirmation: Literal["first_closed_candle_high"] = "first_closed_candle_high"
    retracement_pct: float = Field(default=40.0, gt=0, lt=100)
    no_new_high_minutes: int = Field(default=30, ge=5, le=180)
    maximum_duration_minutes: int = Field(default=180, ge=30, le=720)
    merge_gap_minutes: int = Field(default=30, ge=0, le=180)
    gap_break_candles: int = Field(default=1, ge=1, le=12)
    backfill_days: int = Field(default=180, ge=1, le=730)
    universe_max_assets: int = Field(default=100, ge=1, le=2000)
    volume_filter_enabled: bool = False
    liquidity_filter_enabled: bool = False
    atr_filter_enabled: bool = False
    controls_per_event: int = Field(default=5, ge=1, le=5)
    control_followup_minutes: int = Field(default=180, ge=30, le=720)
    discovery_fraction: float = Field(default=0.70, gt=0, lt=1)
    range_percentiles: list[int] = Field(default_factory=lambda: [10, 25, 50, 75, 90])
    max_hypothesis_conditions: int = Field(default=3, ge=1, le=3)
    timezone: Literal["UTC"] = "UTC"
    operational_profile_mutation_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_contract(self) -> "PumpRadarConfig":
        if self.maximum_duration_minutes < self.maximum_window_minutes:
            raise ValueError("maximum_duration_minutes must cover the detection window")
        if len(self.range_percentiles) != 5 or sorted(set(self.range_percentiles)) != [10, 25, 50, 75, 90]:
            raise ValueError("range_percentiles must be exactly P10/P25/P50/P75/P90")
        if len(set(self.capture_timeframes)) != len(self.capture_timeframes):
            raise ValueError("capture_timeframes cannot contain duplicates")
        return self

    def digest(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PumpRadarRunCreate(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    mode: Literal["incremental", "backfill"] = "incremental"

    @model_validator(mode="after")
    def validate_dates(self) -> "PumpRadarRunCreate":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class PumpRadarHypothesisCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    conditions: list[dict] = Field(min_length=1, max_length=3)
    profile_id: str | None = None
    profile_version_id: str | None = None
    profile_config_hash: str | None = Field(default=None, max_length=128)
