"""Validated aggregate contract for the Strategies settings module."""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


STRATEGY_SETTINGS_SCHEMA = "scalpyn.strategy_settings"
STRATEGY_SETTINGS_SCHEMA_VERSION = 1
ML_SHADOW_KEYS = (
    "shadow_barrier_mode",
    "shadow_atr_timeframe",
    "shadow_atr_multiplier_tp",
    "shadow_atr_multiplier_sl",
    "shadow_barrier_min_pct",
    "shadow_barrier_max_pct",
    "ml_fee_roundtrip_pct",
    "ml_active_barrier_contract_version",
)
ML_SHADOW_OPTIONAL_KEYS = (
    "shadow_measurement_timeframe_priority",
    "shadow_entry_max_lag_seconds",
    "shadow_barrier_geometry_policy",
    "shadow_canonical_barrier_enabled",
    "shadow_canonical_barrier_profile_allowlist",
    "shadow_canonical_barrier_policy_version",
)


class StrategyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    enabled: bool
    params: Dict[str, float] = Field(default_factory=dict)


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategies: List[StrategyDefinition] = Field(default_factory=list)


class MLShadowConfig(BaseModel):
    """The Shadow-owned projection of ``config_type='ml'``."""

    model_config = ConfigDict(extra="forbid")

    shadow_barrier_mode: Literal["FIXED", "ATR_DYNAMIC"] = "ATR_DYNAMIC"
    shadow_atr_timeframe: Literal["1m", "5m", "15m", "1h"] = "5m"
    shadow_atr_multiplier_tp: float = Field(1.5, gt=0, le=100)
    shadow_atr_multiplier_sl: float = Field(1.5, gt=0, le=100)
    shadow_barrier_min_pct: float = Field(0.5, ge=0, le=100)
    shadow_barrier_max_pct: float = Field(3.0, gt=0, le=100)
    ml_fee_roundtrip_pct: float = Field(0.2, ge=0, le=100)
    ml_active_barrier_contract_version: Literal[
        "shadow_fixed_v1", "shadow_atr_dynamic_v2", "shadow_atr_dynamic_v3"
    ] = "shadow_atr_dynamic_v2"
    shadow_barrier_geometry_policy: Literal[
        "LEGACY_INDEPENDENT_CLAMP",
        "SL_ANCHORED_RATIO",
        "ATR_CLAMPED_BEFORE_MULTIPLY",
    ] = "LEGACY_INDEPENDENT_CLAMP"
    shadow_canonical_barrier_enabled: bool = False
    shadow_canonical_barrier_profile_allowlist: List[str] = Field(
        default_factory=list
    )
    shadow_canonical_barrier_policy_version: Literal[
        "shadow_closed_ohlcv_first_touch_v1"
    ] = "shadow_closed_ohlcv_first_touch_v1"
    # Measurement-only controls.  ``None`` is intentional: legacy
    # configurations remain runnable but their captures are UNCONFIGURED and
    # therefore ineligible for training until an operator saves these values.
    shadow_measurement_timeframe_priority: List[
        Literal["1m", "5m", "15m", "1h"]
    ] | None = None
    shadow_entry_max_lag_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_contract_pair(self) -> "MLShadowConfig":
        allowed = (
            {"shadow_atr_dynamic_v2", "shadow_atr_dynamic_v3"}
            if self.shadow_barrier_mode == "ATR_DYNAMIC"
            else {"shadow_fixed_v1"}
        )
        if self.ml_active_barrier_contract_version not in allowed:
            raise ValueError(
                "ml_active_barrier_contract_version is incompatible with "
                f"shadow_barrier_mode; expected one of {sorted(allowed)}"
            )
        if (
            self.ml_active_barrier_contract_version == "shadow_atr_dynamic_v2"
            and self.shadow_barrier_geometry_policy != "LEGACY_INDEPENDENT_CLAMP"
        ):
            raise ValueError(
                "shadow_atr_dynamic_v2 requires LEGACY_INDEPENDENT_CLAMP"
            )
        if self.shadow_barrier_min_pct > self.shadow_barrier_max_pct:
            raise ValueError(
                "shadow_barrier_min_pct must be less than or equal to "
                "shadow_barrier_max_pct"
            )
        return self


class StrategySettingsValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: Dict[str, Any]
    source_hash: str | None = None


class StrategySettingsApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: Dict[str, Any]
    source_hash: str = Field(min_length=64, max_length=64)
    source: Literal["FORM", "JSON_IMPORT"] = "FORM"
    change_description: str = Field(
        default="Updated via Strategies settings module", max_length=500
    )
