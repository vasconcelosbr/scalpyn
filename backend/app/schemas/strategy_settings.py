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
    "shadow_capture_l3_rejected_max_per_hour",
    "shadow_measurement_timeframe_priority",
    "shadow_entry_max_lag_seconds",
    "shadow_barrier_geometry_policy",
    "shadow_canonical_barrier_enabled",
    "shadow_canonical_barrier_profile_allowlist",
    "shadow_canonical_barrier_policy_version",
    "canary_minimum_outcomes",
    "shadow_trailing_contract_version",
    "shadow_trailing_policy_family",
    "shadow_trailing_fixed_activation_profit_pct",
    "shadow_trailing_fixed_hwm_trail_pct",
    "shadow_trailing_stepped_steps",
    "shadow_trailing_stepped_base_activation_profit_pct",
    "shadow_trailing_stepped_base_hwm_trail_pct",
    "shadow_trailing_proportional_k",
)


class ShadowTrailingStep(BaseModel):
    """One rung of the STEPPED trailing ladder: floor jumps to floor_profit_pct
    once high-water-mark profit reaches peak_profit_pct."""

    model_config = ConfigDict(extra="forbid")

    peak_profit_pct: float = Field(gt=0, le=1000)
    floor_profit_pct: float = Field(gt=-100, le=1000)

    @model_validator(mode="after")
    def validate_floor_below_peak(self) -> "ShadowTrailingStep":
        if self.floor_profit_pct >= self.peak_profit_pct:
            raise ValueError(
                "floor_profit_pct must be less than peak_profit_pct for each step"
            )
        return self


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
    # Governance-only observation threshold. It does not participate in TP/SL,
    # scoring, authorization, or outcome calculation.
    canary_minimum_outcomes: int | None = Field(default=None, ge=1, le=10000)
    # Diagnostic capture budget.  The consolidator reads only the persisted
    # value and applies it after ranking canonical winners.
    shadow_capture_l3_rejected_max_per_hour: int = Field(500, ge=0, le=100000)
    # Measurement-only controls.  ``None`` is intentional: legacy
    # configurations remain runnable but their captures are UNCONFIGURED and
    # therefore ineligible for training until an operator saves these values.
    shadow_measurement_timeframe_priority: List[
        Literal["1m", "5m", "15m", "1h"]
    ] | None = None
    shadow_entry_max_lag_seconds: int | None = Field(default=None, ge=0)

    # Shadow-only trailing policy (R1 trailing-policy study). Governs the
    # Shadow exit trailing mechanism exclusively; live-spot selling
    # (spot_engine.sell_flow.trailing) is untouched by these fields.
    # v1 (default) keeps trailing sourced from spot_engine.sell_flow.trailing,
    # exactly as before this contract existed. v2 activates this Shadow-owned
    # policy instead. Succeeds shadow_hwm_trailing_v1.
    shadow_trailing_contract_version: Literal[
        "shadow_hwm_trailing_v1", "shadow_trailing_policy_v2"
    ] = "shadow_hwm_trailing_v1"
    shadow_trailing_policy_family: Literal["FIXED", "STEPPED", "PROPORTIONAL"] = "FIXED"
    shadow_trailing_fixed_activation_profit_pct: float = Field(1.0, ge=0.1, le=100)
    shadow_trailing_fixed_hwm_trail_pct: float = Field(0.35, ge=0.05, le=50)
    shadow_trailing_stepped_steps: List[ShadowTrailingStep] = Field(default_factory=list)
    # Below the first step's peak_profit_pct: None means no trailing at all
    # (hard SL only) until the first step is reached; a pair activates a
    # FIXED-style trail in that region instead.
    shadow_trailing_stepped_base_activation_profit_pct: float | None = Field(
        default=None, ge=0.1, le=100
    )
    shadow_trailing_stepped_base_hwm_trail_pct: float | None = Field(
        default=None, ge=0.05, le=50
    )
    shadow_trailing_proportional_k: float = Field(0.30, gt=0, lt=1)

    @model_validator(mode="after")
    def validate_shadow_trailing_policy(self) -> "MLShadowConfig":
        if self.shadow_trailing_policy_family == "STEPPED":
            if not self.shadow_trailing_stepped_steps:
                raise ValueError(
                    "shadow_trailing_stepped_steps must be non-empty when "
                    "shadow_trailing_policy_family is STEPPED"
                )
            peaks = [s.peak_profit_pct for s in self.shadow_trailing_stepped_steps]
            if peaks != sorted(peaks) or len(set(peaks)) != len(peaks):
                raise ValueError(
                    "shadow_trailing_stepped_steps must have strictly "
                    "increasing peak_profit_pct values"
                )
            base_activation = self.shadow_trailing_stepped_base_activation_profit_pct
            base_trail = self.shadow_trailing_stepped_base_hwm_trail_pct
            if (base_activation is None) != (base_trail is None):
                raise ValueError(
                    "shadow_trailing_stepped_base_activation_profit_pct and "
                    "shadow_trailing_stepped_base_hwm_trail_pct must be both "
                    "set or both null"
                )
        return self

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
