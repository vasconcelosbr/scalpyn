"""Shadow-only policy. Economic values deliberately have no seeded defaults."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = "shadow_l3_continuation_v1"


class ShadowL3ExitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal["shadow_l3_continuation_v1"] = VERSION
    mode: Literal["LEGACY", "OBSERVE", "APPLY"] = "OBSERVE"
    flow_window_seconds: int | None = Field(None, ge=60)
    cvd_window_seconds: int | None = Field(None, ge=60)
    continuation_taker_min: float | None = Field(None, gt=0.5, le=1)
    continuation_delta_min: float | None = Field(None, gt=0, le=1)
    continuation_cvd_min: float | None = Field(None, gt=0, le=1)
    continuation_price_min_pct: float | None = Field(None, gt=0)
    weakening_taker_max: float | None = Field(None, ge=0, lt=1)
    weakening_delta_max: float | None = Field(None, ge=-1, lt=1)
    weakening_cvd_max: float | None = Field(None, ge=-1, lt=1)
    confirmation_seconds: int | None = Field(None, ge=60)
    alignment_seconds: int | None = Field(None, ge=0)
    structure_timeframe: Literal["1m", "5m", "15m"] = "1m"
    pivot_left: int | None = Field(None, ge=1)
    pivot_right: int | None = Field(None, ge=1)
    initial_buffer_pct: float | None = Field(None, gt=0)
    step_trigger_pct: float | None = Field(None, gt=0)
    step_floor_pct: float | None = Field(None, gt=0)
    atr_period: int | None = Field(None, ge=2)
    atr_multiplier: float | None = Field(None, gt=0)
    tight_atr_multiplier: float | None = Field(None, gt=0)
    max_age_seconds: int | None = Field(None, gt=0)
    min_coverage_pct: float | None = Field(None, gt=0, le=100)
    max_gap_seconds: int | None = Field(None, ge=0)
    warmup_seconds: int | None = Field(None, ge=60)
    # Operational defaults, editable and included in each frozen snapshot.
    evaluation_seconds: int = Field(60, ge=60)
    ui_refresh_seconds: int = Field(15, ge=5)
    retention_days: int = Field(30, ge=1)
    trade_batch_size: int = Field(100, ge=1, le=1000)
    capture_batch_size: int = Field(2000, ge=1, le=10000)
    max_candles_per_run: int = Field(120, ge=1, le=1440)
    observation_horizon_seconds: int = Field(86400, ge=60)
    replay_lookback_seconds: int = Field(3600, ge=60)

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_numbers(cls, data):
        if isinstance(data, dict) and any(isinstance(v, bool) for v in data.values()):
            raise ValueError("Boolean values cannot be used as policy parameters")
        return data

    @model_validator(mode="after")
    def coherent(self):
        pairs = [("weakening_taker_max", "continuation_taker_min"),
                 ("weakening_delta_max", "continuation_delta_min"),
                 ("weakening_cvd_max", "continuation_cvd_min")]
        for low, high in pairs:
            a, b = getattr(self, low), getattr(self, high)
            if a is not None and b is not None and a >= b:
                raise ValueError(f"{low} must be below {high}")
        if self.step_floor_pct and self.step_trigger_pct and self.step_floor_pct > self.step_trigger_pct:
            raise ValueError("step_floor_pct must not exceed step_trigger_pct")
        if self.tight_atr_multiplier and self.atr_multiplier and self.tight_atr_multiplier > self.atr_multiplier:
            raise ValueError("tight_atr_multiplier must not exceed atr_multiplier")
        for name in ("flow_window_seconds", "cvd_window_seconds", "confirmation_seconds", "warmup_seconds"):
            value = getattr(self, name)
            if value is not None and value % 60:
                raise ValueError(f"{name} must use complete minute intervals")
        if self.warmup_seconds and any(v and v > self.warmup_seconds for v in (self.flow_window_seconds, self.cvd_window_seconds)):
            raise ValueError("warmup_seconds must cover both flow windows")
        if self.warmup_seconds and self.replay_lookback_seconds < self.warmup_seconds:
            raise ValueError("replay_lookback_seconds must cover the warmup")
        if self.mode == "APPLY" and self.missing_parameters():
            raise ValueError("Missing parameters: " + ", ".join(self.missing_parameters()))
        return self

    def missing_parameters(self) -> list[str]:
        return [name for name in type(self).model_fields if getattr(self, name) is None]

    def digest(self) -> str:
        data = self.model_dump(exclude={"mode"})
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def frozen_policy(config: dict) -> dict:
    policy = ShadowL3ExitPolicy.model_validate(config)
    return {"config": policy.model_dump(), "hash": policy.digest(), "version": VERSION}
