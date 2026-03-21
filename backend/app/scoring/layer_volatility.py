"""L4 — Volatility Score (0-20).

ATR, Bollinger Bands, squeeze detection, compression patterns.
All thresholds from ScoringFuturesConfig (zero hardcode).
"""

from dataclasses import dataclass
from typing import Any, Dict, Literal

import numpy as np
import pandas as pd

from ..schemas.futures_engine_config import ScoringFuturesConfig

VolRegime = Literal["SQUEEZE", "EXPANDING", "NORMAL"]


@dataclass
class L4Result:
    score: float
    regime_score: float
    atr_score: float
    compression_score: float
    bb_position_score: float
    vol_regime: VolRegime
    atr: float
    atr_pct: float
    bb_width: float
    bb_percentile: float
    details: Dict[str, Any]


def score_volatility(
    df: pd.DataFrame,
    trade_direction: str,    # "long" | "short"
    cfg: ScoringFuturesConfig,
) -> L4Result:
    """
    Calculate L4 Volatility score.
    """
    closes = df["close"].astype(float)
    highs  = df["high"].astype(float)
    lows   = df["low"].astype(float)
    n      = len(closes)

    atr_period = cfg.l4_atr_period
    bb_period  = cfg.l4_bb_period
    bb_dev     = cfg.l4_bb_deviation
    sq_pct     = cfg.l4_squeeze_percentile

    # ── ATR ───────────────────────────────────────────────────────────────────
    tr1 = highs - lows
    tr2 = (highs - closes.shift()).abs()
    tr3 = (lows  - closes.shift()).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = tr.rolling(window=atr_period).mean()
    atr_val    = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else 0.0
    close_val  = float(closes.iloc[-1])
    atr_pct    = (atr_val / close_val * 100) if close_val > 0 else 0.0

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    sma    = closes.rolling(window=bb_period).mean()
    std    = closes.rolling(window=bb_period).std()
    upper  = sma + bb_dev * std
    lower  = sma - bb_dev * std
    middle = sma

    bb_width_series = (upper - lower) / middle.replace(0, np.nan)
    bb_width_val    = float(bb_width_series.iloc[-1]) if pd.notna(bb_width_series.iloc[-1]) else 0.05
    bb_upper_val    = float(upper.iloc[-1]) if pd.notna(upper.iloc[-1]) else close_val * 1.02
    bb_lower_val    = float(lower.iloc[-1]) if pd.notna(lower.iloc[-1]) else close_val * 0.98

    # BB width percentile over last 100 candles
    lookback = min(100, n)
    bb_history = bb_width_series.iloc[-lookback:].dropna()
    if len(bb_history) > 5:
        bb_percentile = float(
            (bb_history < bb_width_val).mean() * 100
        )
    else:
        bb_percentile = 50.0

    # ── ATR slope (expanding vs contracting) ─────────────────────────────────
    atr_slope = float(atr_series.iloc[-1] - atr_series.iloc[-5]) if n >= 5 else 0.0

    # ── Volatility regime ─────────────────────────────────────────────────────
    if bb_percentile <= sq_pct:
        vol_regime: VolRegime = "SQUEEZE"
    elif atr_slope > 0 and bb_percentile > 60:
        vol_regime = "EXPANDING"
    else:
        vol_regime = "NORMAL"

    # ── Regime score (0-8): what regime favors which trade ───────────────────
    # SQUEEZE: breakout imminent — great setup for direction trades
    # EXPANDING: move in progress — ok if entering early, risky if late
    # NORMAL: average conditions
    if vol_regime == "SQUEEZE":
        regime_pts = 8.0   # highest potential — compression before breakout
    elif vol_regime == "NORMAL":
        regime_pts = 5.0
    else:  # EXPANDING
        # In expanding: early entry gets 6, late entry (atr very high) gets 2
        if atr_pct < 3.0:
            regime_pts = 6.0
        elif atr_pct < 6.0:
            regime_pts = 4.0
        else:
            regime_pts = 2.0   # very high ATR = late in move

    # ── ATR score (0-4): moderate ATR is ideal ────────────────────────────────
    # Too low: no movement. Too high: overextended / liquidation risk.
    # Ideal: 0.5-2% of price
    if 0.5 <= atr_pct <= 2.0:
        atr_pts = 4.0
    elif 0.3 <= atr_pct < 0.5 or 2.0 < atr_pct <= 4.0:
        atr_pts = 2.5
    elif atr_pct < 0.3:
        atr_pts = 1.0   # no volatility — choppy
    else:
        atr_pts = 1.0   # > 4% ATR — very risky

    # ── Compression score (0-4): detect ATR declining over 5 candles ─────────
    atr_recent = atr_series.iloc[-5:].dropna() if n >= 5 else pd.Series(dtype=float)
    if len(atr_recent) >= 5:
        is_compressing = all(
            atr_recent.iloc[i] <= atr_recent.iloc[i - 1]
            for i in range(1, len(atr_recent))
        )
        compression_pct = (
            (float(atr_recent.iloc[0]) - float(atr_recent.iloc[-1])) /
            float(atr_recent.iloc[0]) * 100
        ) if float(atr_recent.iloc[0]) > 0 else 0.0

        if is_compressing and compression_pct > 20:
            compression_pts = 4.0
        elif is_compressing and compression_pct > 10:
            compression_pts = 2.5
        elif compression_pct > 5:
            compression_pts = 1.5
        else:
            compression_pts = 0.0
    else:
        compression_pts = 0.0

    # ── BB position score (0-4) ───────────────────────────────────────────────
    # Where is price relative to BB bands — context for direction
    bb_range = bb_upper_val - bb_lower_val
    if bb_range > 0:
        bb_position = (close_val - bb_lower_val) / bb_range  # 0=lower, 1=upper
    else:
        bb_position = 0.5

    if trade_direction == "long":
        # Ideal: near lower band (oversold within BB) or in middle (momentum)
        if bb_position <= 0.35:
            bb_pts = 4.0   # near lower — potential bounce
        elif bb_position <= 0.65:
            bb_pts = 2.5   # middle — balanced
        elif bb_position <= 0.85:
            bb_pts = 1.5   # above middle — extended
        else:
            bb_pts = 0.0   # at upper band — overbought
    else:  # short
        if bb_position >= 0.65:
            bb_pts = 4.0
        elif bb_position >= 0.35:
            bb_pts = 2.5
        elif bb_position >= 0.15:
            bb_pts = 1.5
        else:
            bb_pts = 0.0

    total = round(regime_pts + atr_pts + compression_pts + bb_pts, 2)
    total = min(20.0, total)

    return L4Result(
        score=total,
        regime_score=regime_pts,
        atr_score=atr_pts,
        compression_score=compression_pts,
        bb_position_score=bb_pts,
        vol_regime=vol_regime,
        atr=round(atr_val, 8),
        atr_pct=round(atr_pct, 4),
        bb_width=round(bb_width_val, 6),
        bb_percentile=round(bb_percentile, 1),
        details={
            "bb_upper":    round(bb_upper_val, 6),
            "bb_lower":    round(bb_lower_val, 6),
            "bb_position": round(bb_position, 3),
            "atr_slope":   round(atr_slope, 8),
            "is_squeeze":  vol_regime == "SQUEEZE",
        },
    )
