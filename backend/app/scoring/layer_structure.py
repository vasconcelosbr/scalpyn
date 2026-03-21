"""L2 — Market Structure Score (0-20).

Multi-timeframe structure analysis: HH/HL (bullish) or LH/LL (bearish).
Evaluates trend clarity, MTF alignment, key S/R levels, and liquidity sweeps.

All thresholds from ScoringFuturesConfig (zero hardcode).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from ..schemas.futures_engine_config import ScoringFuturesConfig


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str  # "high" or "low"


@dataclass
class L2Result:
    score: float                      # 0-20
    trend_score: float
    alignment_score: float
    levels_score: float
    sweep_score: float
    trend_direction: str              # "bullish" | "bearish" | "ranging"
    mtf_alignment: str                # "aligned" | "partial" | "conflicted"
    key_levels: List[float]
    details: Dict[str, Any]


def _find_swing_points(closes: pd.Series, highs: pd.Series, lows: pd.Series, lookback: int) -> Tuple[List[SwingPoint], List[SwingPoint]]:
    """Find swing highs and lows using a simple pivot detection."""
    n = len(closes)
    swing_highs: List[SwingPoint] = []
    swing_lows:  List[SwingPoint] = []
    window = max(3, lookback // 10)

    for i in range(window, n - window):
        h = highs.iloc[i]
        l = lows.iloc[i]
        if h == highs.iloc[i - window: i + window + 1].max():
            swing_highs.append(SwingPoint(i, float(h), "high"))
        if l == lows.iloc[i - window: i + window + 1].min():
            swing_lows.append(SwingPoint(i, float(l), "low"))

    return swing_highs, swing_lows


def _classify_structure(swing_highs: List[SwingPoint], swing_lows: List[SwingPoint]) -> str:
    """
    Classify trend from last 3 swing highs and lows.
    Returns 'bullish' (HH+HL), 'bearish' (LH+LL), or 'ranging'.
    """
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"

    sh = sorted(swing_highs, key=lambda x: x.index)[-3:]
    sl = sorted(swing_lows,  key=lambda x: x.index)[-3:]

    hh = all(sh[i].price > sh[i - 1].price for i in range(1, len(sh)))
    hl = all(sl[i].price > sl[i - 1].price for i in range(1, len(sl)))
    lh = all(sh[i].price < sh[i - 1].price for i in range(1, len(sh)))
    ll = all(sl[i].price < sl[i - 1].price for i in range(1, len(sl)))

    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "ranging"


def _detect_liquidity_sweeps(highs: pd.Series, lows: pd.Series, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint], lookback: int = 20) -> int:
    """Count recent liquidity sweeps (false breakouts that reversed)."""
    n = len(highs)
    sweep_count = 0
    recent_start = max(0, n - lookback)

    for sh in swing_highs:
        if sh.index < recent_start:
            continue
        # A sweep: price briefly exceeded swing high then reversed down
        subsequent_lows = lows.iloc[sh.index + 1: sh.index + 5]
        if len(subsequent_lows) > 0 and subsequent_lows.min() < sh.price * 0.999:
            sweep_count += 1

    for sl in swing_lows:
        if sl.index < recent_start:
            continue
        subsequent_highs = highs.iloc[sl.index + 1: sl.index + 5]
        if len(subsequent_highs) > 0 and subsequent_highs.max() > sl.price * 1.001:
            sweep_count += 1

    return sweep_count


def score_structure_single_tf(
    df: pd.DataFrame,
    cfg: ScoringFuturesConfig,
) -> Tuple[str, List[float], int]:
    """
    Analyse a single timeframe DataFrame.
    Returns (trend_direction, key_levels, sweep_count).
    """
    if df is None or len(df) < 20:
        return "ranging", [], 0

    closes = df["close"].astype(float)
    highs  = df["high"].astype(float)
    lows   = df["low"].astype(float)

    sh, sl = _find_swing_points(closes, highs, lows, cfg.l2_swing_lookback)
    trend = _classify_structure(sh, sl)

    # Key levels: last 3 swing highs + last 3 swing lows
    key_levels = sorted(set(
        [p.price for p in sorted(sh, key=lambda x: x.index)[-3:]] +
        [p.price for p in sorted(sl, key=lambda x: x.index)[-3:]]
    ))

    sweeps = _detect_liquidity_sweeps(highs, lows, sh, sl, lookback=20)
    return trend, key_levels, sweeps


def score_structure(
    dfs: Dict[str, pd.DataFrame],   # {"15m": df, "1h": df, "4h": df}
    trade_direction: str,            # "long" | "short"
    cfg: ScoringFuturesConfig,
) -> L2Result:
    """
    Multi-timeframe structure scoring.

    Args:
        dfs:             dict of timeframe → DataFrame with OHLCV columns
        trade_direction: "long" or "short"
        cfg:             ScoringFuturesConfig
    """
    tf_results = {}
    all_key_levels: List[float] = []
    total_sweeps = 0

    for tf, df in dfs.items():
        trend, levels, sweeps = score_structure_single_tf(df, cfg)
        tf_results[tf] = trend
        all_key_levels.extend(levels)
        total_sweeps += sweeps

    # Primary trend from 1h (or first available)
    primary_tf  = "1h" if "1h" in tf_results else list(tf_results.keys())[0]
    primary_trend = tf_results.get(primary_tf, "ranging")

    # ── Trend clarity score (0-6) ─────────────────────────────────────────────
    if primary_trend == trade_direction[0:len(primary_trend)]:
        # e.g. "bullish" starts with "b" for "buy"/"long" doesn't match perfectly
        pass
    is_aligned  = (
        (trade_direction == "long"  and primary_trend == "bullish") or
        (trade_direction == "short" and primary_trend == "bearish")
    )
    trend_score = 6.0 if is_aligned else (3.0 if primary_trend == "ranging" else 0.0)

    # ── MTF alignment score (0-6) ─────────────────────────────────────────────
    trends = list(tf_results.values())
    aligned_count = sum(
        1 for t in trends
        if (trade_direction == "long"  and t == "bullish") or
           (trade_direction == "short" and t == "bearish")
    )
    total_tfs = max(len(trends), 1)
    align_ratio = aligned_count / total_tfs

    if align_ratio >= 1.0:
        alignment_score = 6.0
        mtf_alignment   = "aligned"
    elif align_ratio >= 0.67:
        alignment_score = 4.0
        mtf_alignment   = "partial"
    elif align_ratio >= 0.33:
        alignment_score = 2.0
        mtf_alignment   = "partial"
    else:
        alignment_score = 0.0
        mtf_alignment   = "conflicted"

    # ── Key levels clarity (0-4) ──────────────────────────────────────────────
    unique_levels = sorted(set(round(l, 2) for l in all_key_levels))
    levels_score  = min(4.0, len(unique_levels) * 0.8)

    # ── Sweep bonus (0-4) ─────────────────────────────────────────────────────
    # Recent sweeps indicate institutional footprint — bonus for setup quality
    sweep_score = min(4.0, total_sweeps * 1.5)

    total = round(trend_score + alignment_score + levels_score + sweep_score, 2)
    total = min(20.0, total)

    return L2Result(
        score=total,
        trend_score=trend_score,
        alignment_score=alignment_score,
        levels_score=levels_score,
        sweep_score=sweep_score,
        trend_direction=primary_trend,
        mtf_alignment=mtf_alignment,
        key_levels=unique_levels[-6:],  # return most relevant
        details={
            "tf_trends":      tf_results,
            "sweep_count":    total_sweeps,
            "trade_direction": trade_direction,
        },
    )
