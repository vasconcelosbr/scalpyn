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

from typing import Literal, TypedDict

Group = Literal["structural", "microstructure"]
Subtype = Literal["pure", "hybrid"]


class IndicatorClassification(TypedDict):
    group: Group
    subtype: Subtype


# ── Priority 1: Explicit microstructure indicators ───────────────────────────

_MICRO_EXPLICIT: frozenset[str] = frozenset({
    # VWAP (intraday, resets daily → reacts fast on 5m)
    "vwap",
    "vwap_distance_pct",
    # Short-period EMAs (reacts fast enough to justify 5m cadence)
    "ema5",
    "ema9",
    "ema21",
    "ema9_gt_ema21",
    "ema9_distance_pct",
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
    # Market data provenance
    "market_data_source",
    "market_data_confidence",
    "market_data_symbol",
    # Volume 24h from ticker
    "volume_24h_base",
    "volume_24h_usdt",
})

# ── Priority 1: Explicit structural indicators ────────────────────────────────

_STRUCT_EXPLICIT: frozenset[str] = frozenset({
    # RSI / ADX
    "rsi",
    "adx",
    "di_plus",
    "di_minus",
    "adx_acceleration",
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
    "macd_histogram_mean_10",
    "macd_histogram_std_10",
    # Bollinger Bands (20-period SMA)
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_width",
    # Parabolic SAR
    "psar",
    "psar_trend",
    # Z-score
    "zscore",
    # OBV (cumulative — structural by nature)
    "obv",
    # Close / price (from 1h OHLCV structural candle)
    "close",
    "price",
})

# ── Hybrid indicators (depend on both groups' data) ───────────────────────────
# Classified as structural because the slow EMAs are the anchor; the fast
# EMA values are merged in at query time.
_HYBRID_INDICATORS: frozenset[str] = frozenset({
    "ema9_gt_ema50",        # EMA9 (micro) vs EMA50 (structural)
    "ema_full_alignment",   # EMA9 > EMA50 > EMA200 — all three groups
})

# ── FeatureEngine config key → scheduler group ───────────────────────────────
# "ema" appears in BOTH because each scheduler computes different period subsets;
# FeatureEngine.calculate() applies post-compute period filtering per group.

STRUCTURAL_CALC_KEYS: frozenset[str] = frozenset({
    "rsi",
    "adx",
    "ema",           # runs _calc_ema; structural filters keep only EMA50/200
    "atr",
    "macd",
    "bollinger",
    "parabolic_sar",
    "zscore",
    "obv",
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
    """Return the scheduler group for a given indicator name."""
    return classify_indicator_full(name)["group"]


def classify_indicator_full(name: str) -> IndicatorClassification:
    """Return {group, subtype} for a given indicator name.

    Uses the formal priority model described in the module docstring.
    """
    # Priority 1a: explicit microstructure
    if name in _MICRO_EXPLICIT:
        return {"group": "microstructure", "subtype": "pure"}

    # Priority 1b: explicit structural
    if name in _STRUCT_EXPLICIT:
        return {"group": "structural", "subtype": "pure"}

    # Priority 1c: hybrid (cross-group derived)
    if name in _HYBRID_INDICATORS:
        # Hybrid indicators live in the structural group because they anchor
        # on slow EMA values; the fast-EMA component is merged at query time.
        return {"group": "structural", "subtype": "hybrid"}

    # Priority 2: EMA/MA period rule
    # Pattern: ema<period> (e.g. "ema34")
    if name.startswith("ema") and len(name) > 3:
        suffix = name[3:]
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

    # Priority 3: order_flow data source → microstructure
    if name.startswith((
        "market_data_", "orderbook_", "taker_", "funding_", "spread_",
    )):
        return {"group": "microstructure", "subtype": "pure"}

    # Priority 4: ohlcv data source → structural
    # (Most remaining OHLCV-only indicators arrive here via the explicit map
    # above; this catch-all handles future unknowns with ohlcv heritage.)

    # Priority 5: reacts_fast — volume prefix → microstructure
    if name.startswith("volume_"):
        return {"group": "microstructure", "subtype": "pure"}

    # Priority 6: fallback → structural / pure
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
