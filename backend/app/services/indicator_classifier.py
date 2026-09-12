"""Indicator group classifier — dual-scheduler architecture (Task #95).

Determines whether an indicator belongs to the 'structural' group
(slow, 1h-OHLCV, refreshed every 15 min) or the 'microstructure' group
(fast, 5m-OHLCV + live data, refreshed every 5 min).

Classification priority (applied in order — first match wins):
  1. Explicit indicator name map  (covers all known indicator names)
  2. EMA/MA period rule:
       period <= 21  → microstructure / pure
       period 22-49  → structural / hybrid  (conservative)
       period >= 50  → structural / pure
  3. data_source == "order_flow" → microstructure / pure
  4. data_source == "ohlcv"      → structural / pure
  5. reacts_fast == True         → microstructure / pure
  6. Fallback                    → structural / pure

VWAP:
  reset_period="daily" (intraday mode, current default) → microstructure
  reset_period="weekly"/"monthly"/anchored              → structural

Each indicator also gets a subtype:
  "pure"   — belongs unambiguously to one group
  "hybrid" — cross-group derived value (needs both groups' data to be fully
             meaningful, e.g. ema_full_alignment combines EMA9 and EMA200)

FeatureEngine calc-key sets that map config keys to groups:
  STRUCTURAL_CALC_KEYS    — config keys computed by the structural scheduler
  MICROSTRUCTURE_CALC_KEYS — config keys computed by the microstructure scheduler
  Note: "ema" appears in BOTH sets because each scheduler computes a subset of
  EMA periods; post-compute filtering strips the irrelevant periods.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict

Group = Literal["structural", "microstructure"]
Subtype = Literal["pure", "hybrid"]


class IndicatorClassification(TypedDict):
    group: Group
    subtype: Subtype


# ── Priority 1: Explicit microstructure indicators ───────────────────────────
# NOTE: "vwap" and "vwap_distance_pct" are intentionally omitted from this set.
# VWAP classification is mode-aware and handled as Priority 0 (before explicit maps)
# so that mode="daily"|"anchored" can correctly return structural.

_MICRO_EXPLICIT: frozenset[str] = frozenset({
    # Short-period EMAs (reacts fast enough to justify 5m cadence)
    "ema5",
    "ema9",
    "ema21",
    "ema9_gt_ema21",
    "ema5_distance_pct",
    "ema9_distance_pct",
    "ema21_distance_pct",
    "ema50_distance_pct",
    "ema200_distance_pct",
    "vwap_distance_pct",
    "bb_upper_distance_pct",
    "bb_middle_distance_pct",
    "bb_lower_distance_pct",
    "recent_high_5m_distance_pct",
    "recent_high_15m_distance_pct",
    "recent_high_30m_distance_pct",
    "recent_high_1h_distance_pct",
    "recent_low_15m_distance_pct",
    "price_change_1m_pct",
    "price_change_5m_pct",
    "price_change_15m_pct",
    "breakout_distance_pct",
    "recent_high_5m_level",
    "recent_high_5m_reference_time",
    "recent_high_15m_level",
    "recent_high_15m_reference_time",
    "recent_high_30m_level",
    "recent_high_30m_reference_time",
    "recent_high_1h_level",
    "recent_high_1h_reference_time",
    "recent_low_15m_level",
    "recent_low_15m_reference_time",
    # Stochastic (14-period on 5m candles — fast signal)
    "stoch_k",
    "stoch_d",
    # Volume microstructure
    "volume_spike",
    "volume_delta",
    "volume_last_candle_base",
    "volume_last_candle_usdt",
    "volume_24h_candles",
    "volume_24h_coverage_hours",
    "volume_24h_base_aggregated",
    "volume_24h_usdt_aggregated",
    # Order flow / live market data
    "taker_ratio",
    "taker_buy_volume",
    "taker_sell_volume",
    "spread_pct",
    "orderbook_depth_usdt",
    # Order book pressure (top-10 níveis): -1.0 sell pressure / +1.0 buy pressure
    "bid_ask_imbalance",
    "orderbook_pressure",
    # Market data provenance
    "market_data_source",
    "market_data_confidence",
    "market_data_symbol",
    # Volume 24h from ticker
    "volume_24h_base",
    "volume_24h_usdt",
    # Short-term directional structure from the latest closed 5m candles.
    "vwap_reclaim_bool",
    "higher_highs_5",
    "higher_lows_5",
})

# ── Priority 1: Explicit structural indicators ────────────────────────────────

_STRUCT_EXPLICIT: frozenset[str] = frozenset({
    # RSI / ADX
    "rsi",
    "rsi_6",     # multi-period RSI (todos struct: mesmo grupo do `rsi` canônico)
    "rsi_12",
    "rsi_24",
    "adx",
    "di_plus",
    "di_minus",
    "adx_acceleration",
    "adx_slope_3",
    "di_plus_minus_diff",
    "ema21_slope_pct",
    "ema50_slope_pct",
    # Slow EMAs
    "ema50",
    "ema200",
    "ema50_gt_ema200",
    # ATR
    "atr",
    "atr_pct",
    "atr_percent",
    # MACD family (26/12/9 period — slow)
    "macd",
    "macd_signal_line",
    "macd_histogram",
    "macd_signal",
    "macd_histogram_prev",
    "macd_histogram_slope",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "macd_histogram_mean_10",
    "macd_histogram_std_10",
    # Parabolic SAR (canônicos + extensões: ep, af, distance, signal, reversal)
    "psar",
    "psar_trend",
    "psar_ep",
    "psar_af",
    "psar_distance_pct",
    "psar_signal",
    "psar_reversal",
    # Z-score
    "zscore",
    # OBV (cumulative — structural by nature)
    "obv",
    # Close / price (from 1h OHLCV structural candle)
    "close",
    "price",
    # Entry Exhaustion Score — observational Shadow Mode metric (Fase 1)
    # Separado de "exhaustion" do spot_sell_manager (exit-side concept).
    "entry_exhaustion_score",
    "rsi_slope_3",
    "rsi_slope_5",
})

# ── Structural-hybrid indicators (structural group, subtype="hybrid") ─────────
# These indicators are computed using structural OHLCV (1h candles) but their
# period is "intermediate" — not as slow as RSI/ADX, but not as fast as EMA9.
# The task spec explicitly marks Bollinger and anchored VWAP as hybrid.
_STRUCT_HYBRID: frozenset[str] = frozenset({
    # Bollinger Bands (20-period SMA — intermediate period)
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_width",
    # VWAP daily/weekly/anchored (intraday default is microstructure)
    # Note: VWAP name-only lookup never reaches here (Priority 0 intercepts it).
    # This set is used when mode is explicitly "daily"|"weekly"|"anchored".
})

# ── Cross-group hybrid indicators (depend on BOTH groups' data) ───────────────
# Classified as structural because the slow EMAs are the anchor; the fast
# EMA values are merged in at query time.
_HYBRID_INDICATORS: frozenset[str] = frozenset({
    "ema9_gt_ema50",        # EMA9 (micro) vs EMA50 (structural)
    "ema_full_alignment",   # EMA9 > EMA50 > EMA200 — all three groups
    "ema21_ema50_distance_pct",
})

# ── FeatureEngine config key → scheduler group ───────────────────────────────
# "ema" appears in BOTH because each scheduler computes different period subsets;
# FeatureEngine.calculate() applies post-compute period filtering per group.

STRUCTURAL_CALC_KEYS: frozenset[str] = frozenset({
    "rsi",
    "adx",
    "ema",              # runs _calc_ema; structural filters keep only EMA50/200
    "atr",
    "macd",
    "bollinger",
    "parabolic_sar",
    "zscore",
    "obv",
    "entry_exhaustion", # Shadow Mode observational score (Fase 1)
})

MICROSTRUCTURE_CALC_KEYS: frozenset[str] = frozenset({
    "vwap",
    "stochastic",    # fast signal on 5m candles
    "ema",           # runs _calc_ema; micro filters keep only EMA5/9/21
    "volume_spike",
    "volume_delta",
    "volume_metrics",
    "taker_ratio",
})

# EMA period boundaries
_EMA_MICRO_MAX_PERIOD = 21    # EMA periods ≤ 21 → microstructure
_EMA_STRUCT_MIN_PERIOD = 50   # EMA periods ≥ 50 → structural


def classify_indicator(name: str) -> Group:
    """Return the scheduler group for a given indicator name (name-only shorthand)."""
    return classify_indicator_full(name)["group"]


def classify_indicator_full(
    name: str,
    *,
    data_source: Optional[str] = None,
    reacts_fast: Optional[bool] = None,
    mode: Optional[str] = None,
) -> IndicatorClassification:
    """Return {group, subtype} for an indicator using the formal 6-level priority model.

    Args:
        name:        Canonical indicator name (e.g. "rsi", "ema9", "vwap").
        data_source: Optional data lineage hint — "order_flow" | "ohlcv" | None.
        reacts_fast: True if the indicator responds meaningfully within a 5-min window.
        mode:        For VWAP: "intraday" (daily reset) → microstructure;
                     "daily" | "weekly" | "monthly" | "anchored" → structural.

    Priority order (first match wins):
      1. Explicit indicator name map (covers all known indicator names)
      2. EMA/MA period rule (period <= 21 → micro; >= 50 → struct; 22-49 → struct/hybrid)
      3. data_source == "order_flow" → microstructure
      4. data_source == "ohlcv"      → structural
      5. reacts_fast == True         → microstructure
      6. Fallback                    → structural / pure
    """
    # ── Priority 0: VWAP mode-aware classification (runs before explicit maps) ──
    # This must run first so that mode="daily"|"anchored" overrides the default
    # microstructure classification of vwap/vwap_distance_pct.
    if name == "vwap" or name.startswith("vwap_"):
        if mode in ("weekly", "monthly", "anchored", "daily"):
            return {"group": "structural", "subtype": "hybrid"}
        # "intraday" or None → microstructure (default VWAP reset is daily intraday)
        return {"group": "microstructure", "subtype": "pure"}

    # ── Priority 1a: Explicit microstructure names ────────────────────────────
    if name in _MICRO_EXPLICIT:
        return {"group": "microstructure", "subtype": "pure"}

    # ── Priority 1b: Explicit structural names ────────────────────────────────
    if name in _STRUCT_EXPLICIT:
        return {"group": "structural", "subtype": "pure"}

    # ── Priority 1c: Structural-hybrid names (structural group, hybrid subtype) ─
    if name in _STRUCT_HYBRID:
        return {"group": "structural", "subtype": "hybrid"}

    # ── Priority 1d: Cross-group hybrid names (structural group, hybrid subtype) ─
    if name in _HYBRID_INDICATORS:
        return {"group": "structural", "subtype": "hybrid"}

    # ── Priority 2: EMA/SMA/WMA period rule ──────────────────────────────────
    # Applies to ema<period>, sma<period>, wma<period>
    for _prefix in ("ema", "sma", "wma"):
        if name.startswith(_prefix) and len(name) > len(_prefix):
            suffix = name[len(_prefix):]
            try:
                period = int(suffix)
            except ValueError:
                period = None
            if period is not None:
                if period <= _EMA_MICRO_MAX_PERIOD:
                    return {"group": "microstructure", "subtype": "pure"}
                elif period >= _EMA_STRUCT_MIN_PERIOD:
                    return {"group": "structural", "subtype": "pure"}
                else:
                    # 22–49: conservative fallback to structural
                    return {"group": "structural", "subtype": "hybrid"}

    # ── Priority 3: data_source == "order_flow" → microstructure ─────────────
    if data_source == "order_flow":
        return {"group": "microstructure", "subtype": "pure"}

    # Also apply order_flow via name prefix for unknown future indicators
    if name.startswith((
        "market_data_", "orderbook_", "taker_", "funding_", "spread_",
    )):
        return {"group": "microstructure", "subtype": "pure"}

    # ── Priority 4: data_source == "ohlcv" → structural ──────────────────────
    if data_source == "ohlcv":
        return {"group": "structural", "subtype": "pure"}

    # ── Priority 5: reacts_fast == True → microstructure ─────────────────────
    if reacts_fast is True:
        return {"group": "microstructure", "subtype": "pure"}

    # Also apply volume prefix as a name-based reacts_fast proxy
    if name.startswith("volume_"):
        return {"group": "microstructure", "subtype": "pure"}

    # ── Priority 6: Fallback → structural / pure ──────────────────────────────
    return {"group": "structural", "subtype": "pure"}


def classify_calc_key(config_key: str) -> Group:
    """Return the primary scheduler group for a FeatureEngine config key.

    Note: "ema" is ambiguous (appears in both groups) — callers that need
    precise handling should use STRUCTURAL_CALC_KEYS / MICROSTRUCTURE_CALC_KEYS
    directly.
    """
    if config_key in STRUCTURAL_CALC_KEYS and config_key not in MICROSTRUCTURE_CALC_KEYS:
        return "structural"
    if config_key in MICROSTRUCTURE_CALC_KEYS and config_key not in STRUCTURAL_CALC_KEYS:
        return "microstructure"
    # Ambiguous ("ema") or unknown → structural (conservative)
    return "structural"


def is_structural(name: str) -> bool:
    return classify_indicator(name) == "structural"


def is_microstructure(name: str) -> bool:
    return classify_indicator(name) == "microstructure"


# ── Timeframe semantics (AUD-002 follow-up, 2026-09-11) ──────────────────────
# "structural"/"microstructure" above is a scheduler-cadence grouping, not a
# timeframe-identity axis -- both groups can (and do: compute_30m vs
# _compute_structural_5m_async both write scheduler_group='structural')
# contain values computed from more than one OHLCV timeframe for the same
# indicator name. Whether "requested 5m, got 30m" is even a meaningful
# question for a given indicator is a separate, coarser classification:
# does this indicator have a candle-timeframe identity at all, or is it a
# live snapshot / rolling window / composite score instead?
#
# Mirrors two existing, narrower sets kept in pipeline_scan.py (duplicated
# here rather than imported, to avoid a profile_engine/indicator_classifier
# -> pipeline_scan import cycle: pipeline_scan already imports
# profile_engine, which imports this module):
#   _LIVE_ORDER_FLOW_FIELDS        -> ROLLING_WINDOW below
#   _DECISION_CONTEXT_SNAPSHOT_FIELDS -> COMPOSITE below

TimeframeSemantics = Literal[
    "CANDLE_TIMEFRAME", "ROLLING_WINDOW", "LIVE_SNAPSHOT", "COMPOSITE", "UNKNOWN"
]

# Overwritten in-place by _inject_live_order_flow from a rolling trade-tape
# window (pipeline_scan.py: taker_window, e.g. "300s") -- never a fixed
# OHLCV candle, regardless of what group/timeframe last wrote the DB row.
_ROLLING_WINDOW_FIELDS: frozenset[str] = frozenset({
    "taker_ratio", "buy_pressure", "taker_buy_volume", "taker_sell_volume",
    "volume_delta",
})

# Weighted composites of other indicators/scores; "which candle timeframe"
# does not apply to the composite itself (each component may have its own).
_COMPOSITE_FIELDS: frozenset[str] = frozenset({
    "score", "score_raw", "score_max", "score_components",
    "liquidity_score", "market_structure_score", "momentum_score",
    "signal_score", "final_score", "technical_score", "social_score",
})

# Point-in-time order book / ticker snapshot, not a candle aggregate.
_LIVE_SNAPSHOT_PREFIXES: tuple[str, ...] = (
    "market_data_", "orderbook_", "funding_", "spread_", "bid_ask_",
)


def timeframe_semantics(name: str) -> TimeframeSemantics:
    """Does ``name`` have a candle-timeframe identity at all?

    Only ``"CANDLE_TIMEFRAME"`` should ever be compared against a profile
    condition's requested timeframe (AUD-002's ``requested != selected``
    check) or gated by a future ``L3_TIMEFRAME_INTEGRITY_OPERATIONAL``
    promotion. The others have their own, different identity axis (a
    rolling window in seconds, a live snapshot instant, or a formula over
    other fields) and comparing them against "5m vs 30m" is a category
    error, not evidence of a bug.
    """
    if name in _ROLLING_WINDOW_FIELDS:
        return "ROLLING_WINDOW"
    if name in _COMPOSITE_FIELDS:
        return "COMPOSITE"
    if name.startswith(_LIVE_SNAPSHOT_PREFIXES):
        return "LIVE_SNAPSHOT"
    if (
        name in _MICRO_EXPLICIT
        or name in _STRUCT_EXPLICIT
        or name in _STRUCT_HYBRID
        or name in _HYBRID_INDICATORS
    ):
        return "CANDLE_TIMEFRAME"
    for _prefix in ("ema", "sma", "wma"):
        if name.startswith(_prefix) and name[len(_prefix):].isdigit():
            return "CANDLE_TIMEFRAME"
    return "UNKNOWN"


def resolve_candle_timeframe_value(
    asset: dict[str, Any],
    cond: dict[str, Any],
    field: str,
    default_timeframe: str,
    flat_value: Any,
) -> Any:
    """AUD12-001 (2026-09-12 audit): prefer ``asset["_indicators_by_tf"]``
    (Etapa B's exact-identity pre-fetch, ``pipeline_scan.py``, gated by
    ``L3_EXACT_TIMEFRAME_RESOLUTION``) over a flat, potentially
    cross-timeframe merged value, for a ``CANDLE_TIMEFRAME`` field.

    Framework-agnostic equivalent of
    ``ProfileEngine._apply_exact_timeframe_override`` for consumers that
    have no ``ProfileEngine`` instance (and therefore no indicator cache)
    of their own -- namely ``l3_gate_compiler_v2.evaluate_l3_gate_v2``,
    which independently rebuilds its own flat ``eval_data`` and never
    reached the original fix.

    A no-op by construction unless ``asset["_indicators_by_tf"][requested
    timeframe]`` actually has this field.
    """
    if not field or timeframe_semantics(field) != "CANDLE_TIMEFRAME":
        return flat_value
    by_tf = asset.get("_indicators_by_tf")
    if not isinstance(by_tf, dict):
        return flat_value
    tf = cond.get("timeframe") or default_timeframe
    tf_dict = by_tf.get(tf)
    if not isinstance(tf_dict, dict):
        return flat_value
    resolved = tf_dict.get(field)
    return flat_value if resolved is None else resolved
