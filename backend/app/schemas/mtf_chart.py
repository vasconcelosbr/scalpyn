"""Required, versioned configuration for the observational chart, not trading."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

BullPattern = Literal['ENGULF_BULL', 'PIN_BULL', 'MARUBOZU_BULL']
LEGACY_FORCE_PATTERNS = ['ENGULF_BULL', 'MARUBOZU_BULL']
LEGACY_REJECTION_PATTERNS = ['PIN_BULL', 'ENGULF_BULL']


class MTFPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    target_pct: float = Field(default=1, gt=0)
    visible_candles: dict[Literal['4h', '1h', '5m'], int]
    panel_heights: dict[Literal['price', 'volume', 'rsi', 'adx', 'other'], int]
    default_indicators: list[str]
    markers_max: int = Field(ge=1, le=100)
    markers_recent_signals: int = Field(ge=0, le=100)
    pivots_per_type: int = Field(ge=0, le=20)
    marker_min_range_atr: float = Field(ge=0)
    zones_per_side: int = Field(ge=1, le=20)
    removal_confirmations: int = Field(ge=1, le=10)
    retry_initial_seconds: int = Field(ge=1)
    retry_max_seconds: int = Field(ge=1, le=300)
    watchlist_poll_seconds: int = Field(ge=5, le=300)
    autoscale_clip_pct: float = Field(gt=0, lt=25)
    pair_metadata_ttl_seconds: int = Field(ge=60)
    reason_labels: dict[str, str]

    @model_validator(mode='after')
    def complete(self):
        if set(self.visible_candles) != {'4h', '1h', '5m'} or any(v < 10 or v > 1000 for v in self.visible_candles.values()):
            raise ValueError('VISIBLE_CANDLES_INVALID')
        if set(self.panel_heights) != {'price', 'volume', 'rsi', 'adx', 'other'} or any(v < 60 or v > 1200 for v in self.panel_heights.values()):
            raise ValueError('PANEL_HEIGHTS_INVALID')
        if self.retry_initial_seconds > self.retry_max_seconds:
            raise ValueError('RETRY_INTERVAL_INVALID')
        return self


def presentation_preset() -> dict:
    """Explicit editable seed, never merged into persisted engine configuration."""
    return MTFPresentation.model_validate({
        'visible_candles': {'4h': 90, '1h': 120, '5m': 150},
        'panel_heights': {'price': 420, 'volume': 90, 'rsi': 110, 'adx': 110, 'other': 110},
        'default_indicators': ['ema9', 'ema21', 'volume_ma', 'rsi', 'adx'],
        'markers_max': 15, 'markers_recent_signals': 3, 'pivots_per_type': 4,
        'marker_min_range_atr': 0.25, 'zones_per_side': 2, 'removal_confirmations': 2,
        'retry_initial_seconds': 3, 'retry_max_seconds': 60, 'watchlist_poll_seconds': 30,
        'autoscale_clip_pct': 1, 'pair_metadata_ttl_seconds': 3600,
        'reason_labels': {'INVALID_PLAN': 'Plano inválido: entrada, stop ou risco inconsistente', 'BTC_DOWNTREND': 'Bitcoin em tendência de baixa',
            'BTC_CONTEXT_UNAVAILABLE': 'Contexto do Bitcoin indisponível', 'SPOT_NOT_BUYING': 'Viés de baixa: não comprar em Spot',
            'TREND_RANGE': 'Tendência lateral', 'SPREAD_UNAVAILABLE': 'Spread indisponível',
            'SPREAD_TOO_WIDE': 'Spread acima do limite', 'STOP_TOO_WIDE': 'Stop acima do limite',
            'STRUCTURAL_TARGETS_UNAVAILABLE': 'Alvos estruturais indisponíveis', 'RR_BELOW_MIN': 'Retorno/risco abaixo do mínimo',
            'DATA_MISSING': 'Candles ou indicadores insuficientes', 'DATA_STALE': 'Dados de mercado vencidos',
            'EVALUATION_STALE': 'Avaliação vencida', 'CONFIG_CHANGED': 'Configuração alterada; aguardando avaliação'}
    }).model_dump()


class MTFVetoFilters(BaseModel):
    """Legacy-equivalent defaults; switches affect only observational strategy gates."""
    model_config = ConfigDict(extra="forbid")
    asset_downtrend: StrictBool = True
    asset_range: StrictBool = True
    btc_downtrend: StrictBool = True
    spread: StrictBool = True
    stop_distance: StrictBool = True
    risk_reward: StrictBool = True


class MTFPumpFlowConfig(BaseModel):
    """Context-only hypothesis; never part of the MTF engine identity or gates."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: StrictBool
    min_coverage: float = Field(gt=0, le=1)
    max_source_age_seconds: int = Field(ge=1, le=3600)
    max_evaluation_delay_seconds: int = Field(ge=1, le=3600)
    query_timeout_ms: int = Field(ge=100, le=10000)
    capital_neutral_ratio: dict[Literal['4h', '1h', '5m'], Annotated[float, Field(gt=0, le=1)]]
    require_capital: dict[Literal['4h', '1h', '5m'], StrictBool]
    asset_min_delta: float = Field(gt=0, le=1)
    asset_min_buy_persistence: float = Field(gt=0, le=1)

    @model_validator(mode='after')
    def complete(self):
        for values in (self.capital_neutral_ratio, self.require_capital):
            if set(values) != {'4h', '1h', '5m'}:
                raise ValueError('CAPITAL_WINDOWS_REQUIRED')
        if any(not 0 < value <= 1 for value in self.capital_neutral_ratio.values()):
            raise ValueError('CAPITAL_RATIO_OUT_OF_RANGE')
        return self


def pump_flow_preset() -> dict:
    # Editable starting hypothesis, not calibrated strategy parameters.
    return MTFPumpFlowConfig.model_validate({
        'enabled': False, 'min_coverage': .8, 'max_source_age_seconds': 180,
        'query_timeout_ms': 2000, 'max_evaluation_delay_seconds': 600,
        'capital_neutral_ratio': {'4h': .03, '1h': .03, '5m': .03},
        'require_capital': {'4h': False, '1h': True, '5m': False},
        'asset_min_delta': .03, 'asset_min_buy_persistence': .6,
    }).model_dump()


class MTFChartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["mtf_chart_v1", "mtf_chart_v2"]
    presentation: MTFPresentation | None = None
    pump_flow: MTFPumpFlowConfig | None = None
    veto_filters: MTFVetoFilters = Field(default_factory=MTFVetoFilters)
    # None retains the legacy global ATR until the settings are explicitly saved.
    atr_period: int | None = Field(default=None, ge=1)
    force_patterns: list[BullPattern] = Field(default_factory=lambda: list(LEGACY_FORCE_PATTERNS), min_length=1)
    rejection_patterns: list[BullPattern] = Field(default_factory=lambda: list(LEGACY_REJECTION_PATTERNS), min_length=1)
    zone_lookback_candles: dict[Literal['4h', '1h', '5m'], int] | None = None
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
        if self.schema_version == 'mtf_chart_v2':
            if not self.zone_lookback_candles or set(self.zone_lookback_candles) != {'4h','1h','5m'}:
                raise ValueError('ZONE_LOOKBACK_REQUIRED')
            if any(v < 1 or v > self.candle_limits[k] for k,v in self.zone_lookback_candles.items()):
                raise ValueError('ZONE_LOOKBACK_OUT_OF_RANGE')
        if any(n < 50 or n > 1000 for n in self.candle_limits.values()):
            raise ValueError("CANDLE_LIMIT_OUT_OF_RANGE")
        if self.atr_period is not None and self.atr_period >= min(self.candle_limits.values()):
            raise ValueError('ATR_PERIOD_EXCEEDS_HISTORY')
        expected = {"trend", "force", "zone", "rejection", "trigger", "volume", "rr"}
        if set(self.score_weights) != expected or any(v < 0 for v in self.score_weights.values()):
            raise ValueError("SCORE_WEIGHTS_INCOMPLETE")
        if self.min_score > sum(self.score_weights.values()) or not self.trigger_patterns:
            raise ValueError("SCORE_OR_TRIGGERS_INVALID")
        if self.rr_bonus_threshold < self.min_rr_net:
            raise ValueError("RR_BONUS_BELOW_MINIMUM")
        return self

    def engine_dump(self):
        data = self.model_dump(exclude={'presentation', 'pump_flow'})
        # MTF_CONFIGURABLE_VETO_FILTERS: default switches preserve frozen identities.
        if self.veto_filters == MTFVetoFilters():
            data.pop('veto_filters')
        # Preserve legacy identities when the added controls retain old behavior.
        if self.atr_period is None:
            data.pop('atr_period')
        for key, legacy in (('force_patterns', LEGACY_FORCE_PATTERNS), ('rejection_patterns', LEGACY_REJECTION_PATTERNS)):
            if data[key] == legacy:
                data.pop(key)
        if self.schema_version == 'mtf_chart_v1':
            data.pop('zone_lookback_candles', None)
        return data


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
    last_symbol: str | None = None
    last_watchlist: str | None = None
    last_timeframe: Literal['4h','1h','5m'] | None = None
    enabled_indicators: list[str] | None = None
    show_patterns: bool = True
    show_pivots: bool = True
    show_zones: bool = True
    show_target: bool = True
    collapsed_panels: list[str] = Field(default_factory=list)
    clipped_scale: bool = False
