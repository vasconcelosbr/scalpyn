"""Required, versioned configuration for the observational chart, not trading."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from .mtf_rules import (Timeframe, DURATIONS, IndicatorInstance, StageTimeframes, RuleSet,
                        CONTEXT_FIELDS, all_conditions, conditions, rule_uses_flow, validate_condition_types)

BullPattern = Literal['ENGULF_BULL', 'PIN_BULL', 'MARUBOZU_BULL']
LEGACY_FORCE_PATTERNS = ['ENGULF_BULL', 'MARUBOZU_BULL']
LEGACY_REJECTION_PATTERNS = ['PIN_BULL', 'ENGULF_BULL']


class MTFStopSizing(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: StrictBool = False
    target_pct: float = Field(default=1, gt=0, le=100)
    gross_rr: float = Field(default=2, gt=0)

    @model_validator(mode='after')
    def valid_stop(self):
        if not 0 < self.target_pct / self.gross_rr < 100:
            raise ValueError('STOP_DISTANCE_MUST_BE_BELOW_100_PERCENT')
        return self


class MTFFearGreedConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: StrictBool
    refresh_seconds: int = Field(ge=900, le=86400)
    max_age_seconds: int = Field(ge=900, le=604800)

    @model_validator(mode='after')
    def valid_age(self):
        if self.max_age_seconds < self.refresh_seconds:
            raise ValueError('FEAR_GREED_MAX_AGE_BELOW_REFRESH')
        return self


class MTFPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    target_pct: float = Field(default=1, gt=0)
    fear_greed: MTFFearGreedConfig | None = None
    entry_band_enabled: StrictBool | None = None
    visible_candles: dict[Timeframe, int]
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
        if not {'4h', '1h', '5m'} <= set(self.visible_candles) or any(v < 10 or v > 1000 for v in self.visible_candles.values()):
            raise ValueError('VISIBLE_CANDLES_INVALID')
        if set(self.panel_heights) != {'price', 'volume', 'rsi', 'adx', 'other'} or any(v < 60 or v > 1200 for v in self.panel_heights.values()):
            raise ValueError('PANEL_HEIGHTS_INVALID')
        if self.retry_initial_seconds > self.retry_max_seconds:
            raise ValueError('RETRY_INTERVAL_INVALID')
        return self


def presentation_preset() -> dict:
    """Explicit editable seed, never merged into persisted engine configuration."""
    return MTFPresentation.model_validate({
        'fear_greed': {'enabled': True, 'refresh_seconds': 900, 'max_age_seconds': 3600},
        'entry_band_enabled': True,
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


class MTFPumpFlowRevision(BaseModel):
    """One deferred, immutable re-reading of the flow for an evaluation whose flow was
    unavailable at capture (typically read before the Pump cycle published the last
    closed minute). Stored apart from the frozen evaluation; never alters it."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: StrictBool
    delay_seconds: int = Field(ge=1, le=3600)
    batch_limit: int = Field(ge=1, le=5000)


class MTFPumpFlowConfig(BaseModel):
    """Context-only hypothesis; never part of the MTF engine identity or gates."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: StrictBool
    min_coverage: float = Field(gt=0, le=1)
    max_source_age_seconds: int = Field(ge=1, le=3600)
    max_evaluation_delay_seconds: int = Field(ge=1, le=3600)
    query_timeout_ms: int = Field(ge=100, le=10000)
    capital_neutral_ratio: dict[Timeframe, Annotated[float, Field(gt=0, le=1)]]
    require_capital: dict[Timeframe, StrictBool]
    asset_window: Timeframe = '5m'
    asset_min_delta: float = Field(gt=0, le=1)
    asset_min_buy_persistence: float = Field(gt=0, le=1)
    # Optional block: absent means no deferred revision (opt-in, no legacy fallback).
    revision: MTFPumpFlowRevision | None = None

    def identity_dump(self) -> dict:
        """Reading parameters only. The revision schedule never changes what a reading
        means, so it stays out of the flow config_hash and of the engine identity."""
        return self.model_dump(exclude={'revision'})

    @model_validator(mode='after')
    def complete(self):
        for values in (self.capital_neutral_ratio, self.require_capital):
            if not {'4h', '1h', '5m'} <= set(values):
                raise ValueError('CAPITAL_WINDOWS_REQUIRED')
        if set(self.require_capital) != set(self.capital_neutral_ratio):
            raise ValueError('CAPITAL_WINDOWS_MISMATCH')
        if any(not 0 < value <= 1 for value in self.capital_neutral_ratio.values()):
            raise ValueError('CAPITAL_RATIO_OUT_OF_RANGE')
        # A revision later than the freshness limit could never be evaluated.
        if self.revision and self.revision.delay_seconds >= self.max_evaluation_delay_seconds:
            raise ValueError('REVISION_AFTER_FRESHNESS_LIMIT')
        return self


def pump_flow_preset() -> dict:
    # Editable starting hypothesis, not calibrated strategy parameters.
    return MTFPumpFlowConfig.model_validate({
        'enabled': False, 'min_coverage': .8, 'max_source_age_seconds': 180,
        'query_timeout_ms': 2000, 'max_evaluation_delay_seconds': 600,
        'capital_neutral_ratio': {'4h': .03, '1h': .03, '5m': .03},
        'require_capital': {'4h': False, '1h': True, '5m': False},
        'asset_min_delta': .03, 'asset_min_buy_persistence': .6,
        'revision': {'enabled': True, 'delay_seconds': 120, 'batch_limit': 200},
    }).model_dump()


class MTFContextAudit(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: StrictBool = False
    mode: Literal['enforce'] = 'enforce'
    allow_countertrend: Literal[False] = False
    require_structure_alignment: StrictBool = True
    require_zone_alignment: StrictBool = True
    require_structure_integrity: StrictBool = True
    require_trigger_lh_break: StrictBool = True
    require_stop_beyond_trigger_swing: StrictBool = True
    require_stop_beyond_arming_zone: StrictBool = True
    min_stop_atr_trigger: float = Field(default=1.5, gt=0)
    min_reference_rr_gross: float = Field(default=2, gt=0)
    require_positive_reference_reward_net: StrictBool = True


class MTFChartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["mtf_chart_v1", "mtf_chart_v2", "mtf_chart_v3"]
    presentation: MTFPresentation | None = None
    pump_flow: MTFPumpFlowConfig | None = None
    veto_filters: MTFVetoFilters = Field(default_factory=MTFVetoFilters)
    btc_trend_timeframe: Timeframe = "4h"
    timeframes: StageTimeframes | None = None
    indicators: dict[str, IndicatorInstance] | None = None
    frame_atr: dict[Timeframe, str] | None = None
    rules: RuleSet | None = None
    zone_algorithm: Literal['mtf_chart_v1', 'mtf_chart_v2'] | None = None
    structure_ema_period: int = Field(default=50, ge=1, le=1000)
    # None retains the legacy global ATR until the settings are explicitly saved.
    atr_period: int | None = Field(default=None, ge=1)
    force_patterns: list[BullPattern] = Field(default_factory=lambda: list(LEGACY_FORCE_PATTERNS), min_length=1)
    rejection_patterns: list[BullPattern] = Field(default_factory=lambda: list(LEGACY_REJECTION_PATTERNS), min_length=1)
    zone_lookback_candles: dict[Timeframe, int] | None = None
    enabled: bool
    calibrated: Literal[False]
    default_watchlist: str
    monitored_watchlists: list[str]
    candle_limits: dict[Timeframe, int]
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
    stop_sizing: MTFStopSizing = Field(default_factory=MTFStopSizing)
    context_audit: MTFContextAudit = Field(default_factory=MTFContextAudit)
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
        if self.schema_version != "mtf_chart_v3" and self.btc_trend_timeframe not in ("4h","1h"):
            raise ValueError("LEGACY_BTC_TIMEFRAME_UNSUPPORTED")
        if self.schema_version != "mtf_chart_v3" and set(self.candle_limits) != {"4h", "1h", "5m"}:
            raise ValueError("CANDLE_LIMITS_REQUIRED_FOR_ALL_TIMEFRAMES")
        if self.schema_version == 'mtf_chart_v2' or self.zone_algorithm == 'mtf_chart_v2':
            if not self.zone_lookback_candles or set(self.zone_lookback_candles) != set(self.candle_limits):
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
        if self.min_score > sum(self.score_weights.values()) + (sum(r.points for r in self.rules.scoring) if self.rules else 0) or not self.trigger_patterns:
            raise ValueError("SCORE_OR_TRIGGERS_INVALID")
        if self.rr_bonus_threshold < self.min_rr_net:
            raise ValueError("RR_BONUS_BELOW_MINIMUM")
        if self.schema_version == 'mtf_chart_v3':
            if self.timeframes is None or self.indicators is None or self.rules is None or self.zone_algorithm is None or self.frame_atr is None:
                raise ValueError('COMPLETE_MTF_CONTRACT_REQUIRED')
            if self.atr_period is not None: raise ValueError('V3_ATR_PERIOD_IS_DEFINED_BY_FRAME_ATR_INDICATORS')
            needed = set(self.timeframes.model_dump().values()) | {self.btc_trend_timeframe, '4h', '1h', '5m'}
            needed |= {i.timeframe for i in self.indicators.values() if i.enabled}
            if not needed <= set(self.candle_limits): raise ValueError('CANDLE_LIMIT_REQUIRED_FOR_CONFIGURED_TIMEFRAME')
            if self.presentation and not needed <= set(self.presentation.visible_candles): raise ValueError('VISIBLE_CANDLES_REQUIRED_FOR_CONFIGURED_TIMEFRAME')
            for key, indicator in self.indicators.items():
                if not key or '.' in key: raise ValueError('INDICATOR_ID_MUST_NOT_CONTAIN_DOTS')
                if indicator.enabled and indicator.sample_window >= self.candle_limits[indicator.timeframe]:
                    raise ValueError('INDICATOR_HISTORY_TOO_SHORT:'+key)
            if set(self.frame_atr) != set(self.candle_limits): raise ValueError('FRAME_ATR_REQUIRED_FOR_ALL_TIMEFRAMES')
            for tf,key in self.frame_atr.items():
                instance = self.indicators.get(key)
                if not instance or not instance.enabled or instance.family!='atr' or instance.timeframe!=tf:
                    raise ValueError('INVALID_FRAME_ATR:'+tf)
            ids = set()
            for root in [self.rules.armed, self.rules.trigger, *self.rules.vetoes, *(s.when for s in self.rules.scoring)]:
                leaves = list(conditions(root))
                if root in (self.rules.armed, self.rules.trigger) and not any(c.enabled for c in leaves): raise ValueError('RULE_GROUP_REQUIRES_ENABLED_CONDITION')
                for c in leaves:
                    validate_condition_types(c)
                    if c.id in ids: raise ValueError('DUPLICATE_CONDITION_ID:'+c.id)
                    ids.add(c.id)
                    for operand in (c.left,c.right):
                        ref = operand.ref
                        if ref and ref.startswith('indicators.'):
                            key = ref.split('.',1)[1]
                            if key not in self.indicators or (c.enabled and not self.indicators[key].enabled):
                                raise ValueError('INDICATOR_REFERENCE_UNAVAILABLE:'+key)
                        elif ref and ref not in CONTEXT_FIELDS:
                            raise ValueError('UNKNOWN_CONDITION_REFERENCE:'+ref)
                        if c.operator.startswith('crosses_') and ref and not ref.startswith('indicators.'):
                            raise ValueError('CROSS_REQUIRES_INDICATOR_SERIES')
                        if ref and ref.startswith('flow.capital.') and (not self.pump_flow or ref.split('.')[2] not in self.pump_flow.capital_neutral_ratio):
                            raise ValueError('FLOW_WINDOW_NOT_CONFIGURED')
            score_ids = [r.id for r in self.rules.scoring]
            if len(set(score_ids)) != len(score_ids): raise ValueError('DUPLICATE_SCORE_ID')
            if rule_uses_flow(self.rules) and (not self.pump_flow or not self.pump_flow.enabled):
                raise ValueError('FLOW_RULE_REQUIRES_ENABLED_SOURCE')
        elif any(value is not None for value in (self.timeframes,self.indicators,self.rules,self.zone_algorithm,self.frame_atr)):
            raise ValueError('ADVANCED_RULES_REQUIRE_V3')
        return self

    def stage(self, name):
        return getattr(self.timeframes, name) if self.timeframes else {'structure':'4h','zone':'1h','trigger':'5m'}[name]

    def active_timeframes(self):
        return list(self.candle_limits) if self.schema_version == 'mtf_chart_v3' else ['4h','1h','5m']

    def engine_dump(self):
        data = self.model_dump(exclude={'presentation', 'pump_flow'})
        # Disabled sizing preserves all legacy decision identities.
        if not self.stop_sizing.enabled:
            data.pop('stop_sizing', None)
        if not self.context_audit.enabled:
            data.pop('context_audit', None)
        if self.schema_version == 'mtf_chart_v3':
            data.pop('structure_ema_period', None)
            for instance in data['indicators'].values(): instance.pop('display', None)
            if rule_uses_flow(self.rules): data['pump_flow'] = self.pump_flow.identity_dump()
            return data
        for key in ('timeframes','indicators','rules','zone_algorithm','structure_ema_period','frame_atr'):
            data.pop(key, None)
        # MTF_BTC_TIMEFRAME_V1: legacy/default 4h keeps the frozen identity.
        if self.btc_trend_timeframe == "4h":
            data.pop("btc_trend_timeframe")
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
    last_timeframe: Timeframe | None = None
    enabled_indicators: list[str] | None = None
    show_patterns: bool = True
    show_pivots: bool = True
    show_zones: bool = True
    show_target: bool = True
    collapsed_panels: list[str] = Field(default_factory=list)
    clipped_scale: bool = False
