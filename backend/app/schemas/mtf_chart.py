"""Required, versioned configuration for the observational chart, not trading."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MTFChartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["mtf_chart_v1"]
    enabled: bool
    calibrated: Literal[False]
    default_watchlist: str
    monitored_watchlists: list[str]
    candle_limits: dict[str, int]
    poll_seconds: int = Field(ge=5, le=300)
    stale_tf_multiplier: float = Field(ge=1, le=10)
    max_assets_per_watchlist: int = Field(ge=1, le=100)
    pivot_left: int = Field(ge=1, le=20)
    pivot_right: int = Field(ge=1, le=20)
    doji_body_pct: float = Field(gt=0, lt=1)
    pin_wick_ratio: float = Field(gt=0)
    pin_wick_pct: float = Field(gt=0, lt=1)
    pin_close_fraction: float = Field(gt=0, lt=1)
    engulf_include_wicks: bool
    marubozu_wick_pct: float = Field(gt=0, lt=1)
    min_range_atr: float = Field(gt=0)
    vol_mult: float = Field(gt=0)
    vol_lookback: int = Field(ge=2, le=200)
    zone_atr_mult: float = Field(gt=0)
    zone_min_touches: int = Field(ge=2, le=20)
    zone_proximity_atr: float = Field(ge=0)
    rejection_lookback: int = Field(ge=1, le=20)
    stop_buffer_atr: float = Field(gt=0)
    max_stop_atr: float = Field(gt=0)
    min_rr_net: float = Field(gt=0)
    rr_bonus_threshold: float = Field(gt=0)
    max_spread_pct: float = Field(gt=0)
    fee_per_side_pct: float = Field(ge=0, lt=10)
    slippage_pct: float = Field(ge=0, lt=10)
    capital_usdt: float = Field(gt=0)
    risk_per_trade_pct: float = Field(gt=0, le=100)
    partial_tp1_pct: float = Field(gt=0, lt=100)
    expires_after_candles: int = Field(ge=1, le=100)
    holding_candles: int = Field(ge=1, le=1000)
    min_score: float = Field(ge=0)
    score_weights: dict[str, float]
    trigger_patterns: list[Literal["ENGULF_BULL", "PIN_BULL", "MARUBOZU_BULL"]]
    history_page_size: int = Field(ge=1, le=100)
    confidence_level: float = Field(gt=0.5, lt=1)
    min_report_sample: int = Field(ge=2)

    @model_validator(mode="after")
    def complete(self):
        if set(self.candle_limits) != {"4h", "1h", "5m"}:
            raise ValueError("CANDLE_LIMITS_REQUIRED_FOR_ALL_TIMEFRAMES")
        if any(n < 50 or n > 1000 for n in self.candle_limits.values()):
            raise ValueError("CANDLE_LIMIT_OUT_OF_RANGE")
        expected = {"trend", "force", "zone", "rejection", "trigger", "volume", "rr"}
        if set(self.score_weights) != expected or any(v < 0 for v in self.score_weights.values()):
            raise ValueError("SCORE_WEIGHTS_INCOMPLETE")
        if self.min_score > sum(self.score_weights.values()) or not self.trigger_patterns:
            raise ValueError("SCORE_OR_TRIGGERS_INVALID")
        if self.rr_bonus_threshold < self.min_rr_net:
            raise ValueError("RR_BONUS_BELOW_MINIMUM")
        return self


def experimental_preset() -> dict:
    # Explicit seed only. Runtime NEVER merges this with a partial saved config.
    return MTFChartConfig.model_validate({
        "schema_version": "mtf_chart_v1", "enabled": True, "calibrated": False,
        "default_watchlist": "l3-consolidated", "monitored_watchlists": ["l3-consolidated"],
        "candle_limits": {"4h": 300, "1h": 300, "5m": 500},
        "poll_seconds": 15, "stale_tf_multiplier": 2, "max_assets_per_watchlist": 100,
        "pivot_left": 3, "pivot_right": 3, "doji_body_pct": 0.1,
        "pin_wick_ratio": 2, "pin_wick_pct": 0.6, "pin_close_fraction": 1 / 3,
        "engulf_include_wicks": False, "marubozu_wick_pct": 0.1, "min_range_atr": 0.25,
        "vol_mult": 1.5, "vol_lookback": 20, "zone_atr_mult": 0.5,
        "zone_min_touches": 2, "zone_proximity_atr": 0.75, "rejection_lookback": 3,
        "stop_buffer_atr": 0.2, "max_stop_atr": 3, "min_rr_net": 2.5,
        "rr_bonus_threshold": 3, "max_spread_pct": 0.2,
        "fee_per_side_pct": 0.1, "slippage_pct": 0.05,
        "capital_usdt": 10000, "risk_per_trade_pct": 1, "partial_tp1_pct": 50,
        "expires_after_candles": 3, "holding_candles": 36, "min_score": 7,
        "score_weights": {"trend": 2, "force": 1, "zone": 2, "rejection": 1,
                          "trigger": 2, "volume": 1, "rr": 1},
        "trigger_patterns": ["ENGULF_BULL", "PIN_BULL", "MARUBOZU_BULL"],
        "history_page_size": 30, "confidence_level": 0.95, "min_report_sample": 50,
    }).model_dump()


class MTFPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: Literal["dark", "light"]
    timezone: Literal["America/Sao_Paulo", "UTC"]
    sound: bool
    aligned_only: bool
