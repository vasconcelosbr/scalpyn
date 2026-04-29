"""Indicator group classifier for the dual-scheduler architecture.

Determines whether an indicator belongs to the 'structural' group
(slow indicators, computed from 1h OHLCV, refreshed every 15 min)
or the 'microstructure' group (fast indicators, computed from 5m OHLCV
+ live market data, refreshed every 5 min).

Priority order for classify_indicator():
  1. Explicit allow-list (covers all known indicator names)
  2. Prefix rules (ema*, market_data_*, orderbook_*, volume_*)
  3. Fallback → structural

For classify_calc_key():
  Maps FeatureEngine config keys ('rsi', 'macd', 'vwap', …) to a group
  so the scheduler can call FeatureEngine.calculate(df, group='structural')
  and only compute indicators for that group.
"""

from __future__ import annotations

from typing import Literal

Group = Literal["structural", "microstructure"]

# ── Explicit indicator → group mapping ───────────────────────────────────────

_MICROSTRUCTURE_INDICATORS: frozenset[str] = frozenset({
    # VWAP (intraday, resets daily → reacts fast on 5m)
    "vwap",
    "vwap_distance_pct",
    # Volume microstructure
    "volume_spike",
    "volume_delta",
    "volume_last_candle_base",
    "volume_last_candle_usdt",
    "volume_24h_candles",
    "volume_24h_coverage_hours",
    "volume_24h_base_aggregated",
    "volume_24h_usdt_aggregated",
    # Order flow (live market data overrides)
    "taker_ratio",
    "taker_buy_volume",
    "taker_sell_volume",
    "spread_pct",
    "orderbook_depth_usdt",
    # Market data provenance
    "market_data_source",
    "market_data_confidence",
    "market_data_symbol",
    # Volume 24h from ticker (updated by microstructure scheduler)
    "volume_24h_base",
    "volume_24h_usdt",
})

_STRUCTURAL_INDICATORS: frozenset[str] = frozenset({
    # RSI / ADX
    "rsi",
    "adx",
    "di_plus",
    "di_minus",
    "adx_acceleration",
    # EMA values
    "ema5",
    "ema9",
    "ema21",
    "ema50",
    "ema200",
    # EMA-derived booleans
    "ema9_gt_ema21",
    "ema9_gt_ema50",
    "ema50_gt_ema200",
    "ema_full_alignment",
    "ema9_distance_pct",
    # ATR
    "atr",
    "atr_pct",
    "atr_percent",
    # MACD family
    "macd",
    "macd_signal_line",
    "macd_histogram",
    "macd_signal",
    "macd_histogram_prev",
    "macd_histogram_slope",
    "macd_histogram_mean_10",
    "macd_histogram_std_10",
    # Bollinger Bands
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_width",
    # Parabolic SAR
    "psar",
    "psar_trend",
    # Z-score
    "zscore",
    # OBV
    "obv",
    # Stochastic
    "stoch_k",
    "stoch_d",
    # Close / price (from 1h OHLCV)
    "close",
    "price",
})

# ── FeatureEngine config key → group ─────────────────────────────────────────
# These are the keys in indicators_config / DEFAULT_INDICATORS that map to
# the individual _calc_* methods inside FeatureEngine.

STRUCTURAL_CALC_KEYS: frozenset[str] = frozenset({
    "rsi",
    "adx",
    "ema",
    "atr",
    "macd",
    "stochastic",
    "bollinger",
    "parabolic_sar",
    "zscore",
    "obv",
})

MICROSTRUCTURE_CALC_KEYS: frozenset[str] = frozenset({
    "vwap",
    "volume_spike",
    "volume_delta",
    "volume_metrics",
    "taker_ratio",
})

# Config keys that do not cleanly belong to either group (order-book imbalance,
# funding rate, btc_dominance) are computed by the microstructure scheduler
# since they also rely on live market data.
_AMBIGUOUS_AS_MICROSTRUCTURE: frozenset[str] = frozenset({
    "orderbook_imbalance",
    "funding_rate",
    "btc_dominance",
    "market_data_fallback",
})


def classify_indicator(name: str) -> Group:
    """Return the scheduler group for a given indicator name.

    Args:
        name: The raw indicator key (e.g. 'rsi', 'vwap_distance_pct').

    Returns:
        'structural' or 'microstructure'.
    """
    if name in _MICROSTRUCTURE_INDICATORS:
        return "microstructure"
    if name in _STRUCTURAL_INDICATORS:
        return "structural"

    # Prefix rules
    if name.startswith("ema"):
        return "structural"
    if name.startswith(("market_data_", "orderbook_", "taker_", "funding_")):
        return "microstructure"
    if name.startswith("volume_"):
        return "microstructure"

    # Fallback
    return "structural"


def classify_calc_key(config_key: str) -> Group:
    """Return the scheduler group for a FeatureEngine config key.

    Args:
        config_key: e.g. 'rsi', 'vwap', 'volume_metrics'.

    Returns:
        'structural' or 'microstructure'.
    """
    if config_key in STRUCTURAL_CALC_KEYS:
        return "structural"
    if config_key in MICROSTRUCTURE_CALC_KEYS:
        return "microstructure"
    if config_key in _AMBIGUOUS_AS_MICROSTRUCTURE:
        return "microstructure"
    return "structural"


def is_structural(name: str) -> bool:
    return classify_indicator(name) == "structural"


def is_microstructure(name: str) -> bool:
    return classify_indicator(name) == "microstructure"
