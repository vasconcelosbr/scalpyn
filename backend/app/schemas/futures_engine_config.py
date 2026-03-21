"""Pydantic schemas for the Futures Engine config.

Config is stored as separate config_type entries in ConfigProfile,
but loaded as a unified FuturesEngineConfig for the engine.

Config types:
  risk_futures           → RiskFuturesConfig
  macro_regime_futures   → MacroRegimeConfig
  scoring_futures        → ScoringFuturesConfig
  leverage_checks_futures → LeverageChecksConfig
  execution_futures      → ExecutionFuturesConfig
  management_futures     → ManagementFuturesConfig
"""

from pydantic import BaseModel, Field
from typing import Dict, List, Literal, Optional


# ── Risk ──────────────────────────────────────────────────────────────────────

class RiskFuturesConfig(BaseModel):
    max_risk_per_trade_pct: float = Field(1.0, ge=0.1, le=10)
    max_risk_per_trade_conviction_pct: float = Field(2.0, ge=0.1, le=10)
    max_total_risk_pct: float = Field(5.0, ge=1, le=50)
    daily_loss_limit_pct: float = Field(3.0, ge=0.5, le=20)
    weekly_loss_limit_pct: float = Field(5.0, ge=1, le=30)
    circuit_breaker_consecutive_losses: int = Field(3, ge=1, le=20)
    circuit_breaker_pause_minutes: int = Field(60, ge=5)
    max_positions: int = Field(5, ge=1, le=50)
    max_correlated_positions: int = Field(2, ge=1, le=10)
    max_capital_deployed_pct: float = Field(60.0, ge=10, le=100)
    correlation_threshold: float = Field(0.7, ge=0, le=1)
    correlation_lookback_days: int = Field(30, ge=7)


# ── Macro Regime ──────────────────────────────────────────────────────────────

class MacroWeights(BaseModel):
    btc_trend: int = Field(30, ge=0, le=100)
    dxy_direction: int = Field(20, ge=0, le=100)
    funding_rate_market: int = Field(15, ge=0, le=100)
    liquidation_pressure: int = Field(15, ge=0, le=100)
    stablecoin_flow: int = Field(10, ge=0, le=100)
    vix_risk_appetite: int = Field(10, ge=0, le=100)


class MacroThresholds(BaseModel):
    strong_risk_on: float = Field(75.0, ge=50, le=100)
    risk_on: float = Field(55.0, ge=30, le=80)
    neutral: float = Field(40.0, ge=20, le=60)
    risk_off: float = Field(25.0, ge=0, le=45)


class MacroRegimeConfig(BaseModel):
    enabled: bool = True
    update_interval_minutes: int = Field(30, ge=5)
    weights: MacroWeights = Field(default_factory=MacroWeights)
    thresholds: MacroThresholds = Field(default_factory=MacroThresholds)
    risk_off_allow_long_min_score: float = Field(85.0, ge=70, le=100)
    risk_on_allow_short: bool = True
    risk_on_short_size_reduction: float = Field(0.50, ge=0, le=1)
    neutral_size_reduction: float = Field(0.25, ge=0, le=1)
    pre_event_buffer_hours: float = Field(4.0, ge=0)
    pre_event_size_reduction: float = Field(0.50, ge=0, le=1)
    btc_ema_periods: List[int] = Field(default_factory=lambda: [21, 50, 200])
    btc_timeframe: str = "1d"
    dxy_ema_period: int = Field(21, ge=5)
    funding_extreme_positive: float = Field(0.05, ge=0)
    funding_extreme_negative: float = Field(-0.03, le=0)
    vix_elevated: float = Field(25.0, ge=10)
    vix_panic: float = Field(35.0, ge=20)


# ── Scoring ───────────────────────────────────────────────────────────────────

class L1Weights(BaseModel):
    volume_24h: float = Field(7.0, ge=0, le=20)
    relative_volume: float = Field(5.0, ge=0, le=20)
    spread: float = Field(4.0, ge=0, le=20)
    book_depth: float = Field(4.0, ge=0, le=20)


class ScoringFuturesConfig(BaseModel):
    min_score_to_trade: float = Field(70.0, ge=0, le=100)
    min_layer_score: float = Field(8.0, ge=0, le=20)
    conviction_threshold: float = Field(90.0, ge=70, le=100)
    strong_threshold: float = Field(80.0, ge=60, le=95)
    valid_threshold: float = Field(70.0, ge=50, le=85)
    size_multipliers: Dict[str, float] = Field(
        default_factory=lambda: {"institutional_grade": 1.5, "strong": 1.0, "valid": 0.6}
    )
    leverage_tiers: Dict[str, float] = Field(
        default_factory=lambda: {"high": 1.0, "normal": 0.7, "conservative": 0.4}
    )
    l1_hard_reject: float = Field(10.0, ge=0, le=20)
    l1_weights: L1Weights = Field(default_factory=L1Weights)
    l2_timeframes: List[str] = Field(default_factory=lambda: ["15m", "1h", "4h"])
    l2_swing_lookback: int = Field(50, ge=10)
    l3_rsi_period: int = Field(14, ge=5)
    l3_divergence_lookback: int = Field(20, ge=5)
    l4_atr_period: int = Field(14, ge=5)
    l4_bb_period: int = Field(20, ge=10)
    l4_bb_deviation: float = Field(2.0, ge=1)
    l4_squeeze_percentile: float = Field(20.0, ge=5, le=50)
    l5_funding_extreme_positive: float = Field(0.05, ge=0)
    l5_funding_extreme_negative: float = Field(-0.05, le=0)
    l5_whale_threshold_usd: float = Field(100000.0, ge=1000)


# ── Leverage Checks ───────────────────────────────────────────────────────────

class FundingGuardConfig(BaseModel):
    enabled: bool = True
    funding_max_for_long: float = Field(0.03, ge=0)
    funding_min_for_short: float = Field(-0.03, le=0)
    funding_extreme: float = Field(0.05, ge=0)
    funding_reduction_pct: float = Field(0.30, ge=0, le=1)
    max_funding_cost_pct_of_profit: float = Field(0.15, ge=0, le=1)


class OIGuardConfig(BaseModel):
    enabled: bool = True
    oi_extreme_percentile: float = Field(95.0, ge=50, le=100)
    oi_reduction_pct: float = Field(0.30, ge=0, le=1)
    oi_lookback_days: int = Field(30, ge=7)
    oi_stop_tighten_pct: float = Field(0.20, ge=0, le=1)


class LiquidationGuardConfig(BaseModel):
    enabled: bool = True
    min_liquidation_distance_pct: float = Field(15.0, ge=5, le=50)
    adjust_stop_beyond_cluster: bool = True
    cluster_proximity_pct: float = Field(2.0, ge=0.5)


class LeverageChecksConfig(BaseModel):
    funding_guard: FundingGuardConfig = Field(default_factory=FundingGuardConfig)
    oi_guard: OIGuardConfig = Field(default_factory=OIGuardConfig)
    liquidation_guard: LiquidationGuardConfig = Field(default_factory=LiquidationGuardConfig)


# ── Execution ─────────────────────────────────────────────────────────────────

class EntryConfig(BaseModel):
    default_order_type: Literal["limit", "market"] = "limit"
    entry_timeout_minutes: int = Field(30, ge=1)
    max_slippage_pct: float = Field(0.10, ge=0)


class StopLossConfig(BaseModel):
    method_priority: List[str] = Field(default_factory=lambda: ["structure", "liquidity", "atr"])
    atr_stop_multiplier: float = Field(1.5, ge=0.5, le=5)
    max_stop_distance_pct: float = Field(5.0, ge=0.5, le=20)
    min_stop_distance_pct: float = Field(0.3, ge=0.1)
    liq_price_safety_margin_pct: float = Field(3.0, ge=1, le=20)


class TakeProfitConfig(BaseModel):
    rr_tp1: float = Field(1.5, ge=0.5, le=10)
    rr_tp2: float = Field(2.5, ge=1, le=20)
    rr_tp3: float = Field(4.0, ge=2, le=30)
    tp1_exit_pct: float = Field(35.0, ge=10, le=90)
    tp2_exit_pct: float = Field(50.0, ge=10, le=90)
    move_stop_to_breakeven_at: Literal["tp1", "tp2", "never"] = "tp1"
    activate_trailing_at: Literal["tp1", "tp2"] = "tp2"
    squeeze_tp_multiplier: float = Field(1.3, ge=1, le=3)
    expanding_tp_multiplier: float = Field(0.85, ge=0.5, le=1)


class LeverageCapsConfig(BaseModel):
    max_leverage_institutional: float = Field(10.0, ge=1, le=200)
    max_leverage_strong: float = Field(7.0, ge=1, le=200)
    max_leverage_valid: float = Field(4.0, ge=1, le=200)
    max_leverage_risk_off: float = Field(3.0, ge=1, le=200)
    min_liquidation_distance_from_stop_pct: float = Field(5.0, ge=1, le=30)


class ExecutionFuturesConfig(BaseModel):
    entry: EntryConfig = Field(default_factory=EntryConfig)
    stop_loss: StopLossConfig = Field(default_factory=StopLossConfig)
    take_profit: TakeProfitConfig = Field(default_factory=TakeProfitConfig)
    leverage: LeverageCapsConfig = Field(default_factory=LeverageCapsConfig)
    max_capital_per_trade_pct: float = Field(30.0, ge=1, le=100)
    max_capital_deployed_pct: float = Field(60.0, ge=10, le=100)


# ── Management ────────────────────────────────────────────────────────────────

class TrailingManagementConfig(BaseModel):
    method: Literal["atr", "fixed"] = "atr"
    trailing_atr_multiplier: float = Field(1.0, ge=0.3, le=5)
    tighten_above_profit_pct: float = Field(5.0, ge=1, le=50)
    tighten_factor: float = Field(0.7, ge=0.3, le=0.99)


class EmergencyConfig(BaseModel):
    macro_shift_exit: bool = True
    btc_emergency_threshold_1h_pct: float = Field(4.0, ge=1, le=20)
    funding_emergency: float = Field(0.08, ge=0)
    emergency_liq_distance_pct: float = Field(5.0, ge=1, le=20)
    max_exchange_latency_ms: int = Field(5000, ge=500)


class FundingDrainConfig(BaseModel):
    enabled: bool = True
    max_funding_drain_pct_of_profit: float = Field(0.25, ge=0.05, le=1)
    max_daily_funding_cost_usd: float = Field(50.0, ge=1)
    warn_on_adverse_funding: bool = True


class PartialExitsConfig(BaseModel):
    tp1_close_pct: float = Field(35.0, ge=10, le=90)
    tp2_close_pct: float = Field(50.0, ge=10, le=90)
    trailing_remainder_pct: float = Field(15.0, ge=5, le=50)


class ManagementFuturesConfig(BaseModel):
    trailing: TrailingManagementConfig = Field(default_factory=TrailingManagementConfig)
    emergency: EmergencyConfig = Field(default_factory=EmergencyConfig)
    funding_drain: FundingDrainConfig = Field(default_factory=FundingDrainConfig)
    partial_exits: PartialExitsConfig = Field(default_factory=PartialExitsConfig)


# ── Unified Config ────────────────────────────────────────────────────────────

class FuturesEngineConfig(BaseModel):
    """
    Unified config for the Futures Engine.
    Can be loaded from a single 'futures_engine' config_type or
    composed from multiple separate config_type entries.
    """
    risk: RiskFuturesConfig = Field(default_factory=RiskFuturesConfig)
    macro: MacroRegimeConfig = Field(default_factory=MacroRegimeConfig)
    scoring: ScoringFuturesConfig = Field(default_factory=ScoringFuturesConfig)
    leverage_checks: LeverageChecksConfig = Field(default_factory=LeverageChecksConfig)
    execution: ExecutionFuturesConfig = Field(default_factory=ExecutionFuturesConfig)
    management: ManagementFuturesConfig = Field(default_factory=ManagementFuturesConfig)

    @classmethod
    def from_config_json(cls, config_json: dict) -> "FuturesEngineConfig":
        return cls(**config_json)

    def default_json(self) -> dict:
        return self.model_dump()
