"""Pydantic schemas for the Spot Engine config (config_type = 'spot_engine').

All thresholds and parameters come from the DB (ConfigProfile.config_json).
Zero hardcode — every numeric default here is the UI default, not a constant.
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class ScannerConfig(BaseModel):
    scan_interval_seconds: int = Field(30, ge=5, le=3600)
    universe_source: Literal["dynamic", "watchlist", "custom"] = "dynamic"
    buy_threshold_score: float = Field(75.0, ge=0, le=100)
    strong_buy_threshold: float = Field(85.0, ge=0, le=100)
    max_opportunities_per_scan: int = Field(3, ge=1, le=20)
    symbol_cooldown_seconds: int = Field(300, ge=0)
    global_cooldown_after_n_buys: int = Field(0, ge=0)


class BuyingConfig(BaseModel):
    capital_per_trade_pct: float = Field(10.0, ge=0.1, le=100)
    capital_per_trade_min_usdt: float = Field(20.0, ge=1)
    capital_reserve_pct: float = Field(10.0, ge=0, le=99)
    max_capital_in_use_pct: float = Field(80.0, ge=10, le=100)
    max_positions_total: int = Field(20, ge=1, le=500)
    max_positions_per_asset: int = Field(5, ge=1, le=50)
    max_exposure_per_asset_pct: float = Field(25.0, ge=1, le=100)
    order_type: Literal["market", "limit"] = "market"
    limit_order_timeout_seconds: int = Field(120, ge=10)


class SellingConfig(BaseModel):
    take_profit_pct: float = Field(1.5, ge=0.1, le=1000)
    min_profit_pct: float = Field(0.5, ge=0.0, le=1000)
    never_sell_at_loss: bool = True            # INVIOLÁVEL — never False in prod
    safety_margin_above_entry_pct: float = Field(0.3, ge=0)
    enable_ai_consultation: bool = False
    ai_rate_limit_seconds: int = Field(60, ge=10)
    ai_model: str = "google/gemini-2.5-flash"


class HoldingUnderwaterConfig(BaseModel):
    alert_after_hours: float = Field(24.0, ge=1)
    alert_repeat_interval_hours: float = Field(12.0, ge=1)
    track_opportunity_cost: bool = True


class DCAConfig(BaseModel):
    enabled: bool = False
    trigger_drop_pct: float = Field(5.0, ge=0.5, le=50)
    min_score_for_dca: float = Field(70.0, ge=0, le=100)
    max_dca_layers: int = Field(3, ge=1, le=10)
    dca_amount_usdt: float = Field(50.0, ge=1)
    dca_decay_factor: float = Field(0.7, ge=0.1, le=1.0)
    max_total_exposure_per_asset_pct: float = Field(30.0, ge=1, le=100)


class RangingConfig(BaseModel):
    enabled: bool = True
    adx_threshold: float = Field(18.0, ge=5, le=40)
    bb_width_threshold: float = Field(0.03, ge=0.001)


class ExhaustionConfig(BaseModel):
    enabled: bool = True
    rsi_overbought: float = Field(72.0, ge=50, le=100)
    volume_decline_pct: float = Field(20.0, ge=5, le=80)


class AIConsultationConfig(BaseModel):
    enabled: bool = False
    trigger_profit_pct: float = Field(1.0, ge=0)


class TargetConfig(BaseModel):
    volatility_filter_enabled: bool = True
    min_volume_multiplier: float = Field(0.8, ge=0.1)
    liquidity_check_enabled: bool = True


class TrailingConfig(BaseModel):
    enabled: bool = False
    hwm_trail_pct: float = Field(0.5, ge=0.1, le=50)
    activation_profit_pct: float = Field(2.0, ge=0.1)


class SellFlowConfig(BaseModel):
    ranging: RangingConfig = Field(default_factory=RangingConfig)
    exhaustion: ExhaustionConfig = Field(default_factory=ExhaustionConfig)
    ai_consultation: AIConsultationConfig = Field(default_factory=AIConsultationConfig)
    target: TargetConfig = Field(default_factory=TargetConfig)
    trailing: TrailingConfig = Field(default_factory=TrailingConfig)


class MacroFilterConfig(BaseModel):
    enabled: bool = False
    block_in_risk_off: bool = True


class SpotEngineConfig(BaseModel):
    """
    Full config for the Spot Engine.
    Loaded from ConfigProfile where config_type = 'spot_engine'.
    Every field is GUI-editable; zero hardcode in engine code.
    """

    scanner: ScannerConfig = Field(default_factory=ScannerConfig)
    buying: BuyingConfig = Field(default_factory=BuyingConfig)
    selling: SellingConfig = Field(default_factory=SellingConfig)
    holding_underwater: HoldingUnderwaterConfig = Field(default_factory=HoldingUnderwaterConfig)
    dca: DCAConfig = Field(default_factory=DCAConfig)
    sell_flow: SellFlowConfig = Field(default_factory=SellFlowConfig)
    macro_filter: MacroFilterConfig = Field(default_factory=MacroFilterConfig)

    @classmethod
    def from_config_json(cls, config_json: dict) -> "SpotEngineConfig":
        return cls(**config_json)

    def default_json(self) -> dict:
        """Return the full default config as dict (for DB seeding)."""
        return self.model_dump()
