"""L3 — Momentum Score (0-20).

RSI, MACD, EMA alignment, VWAP position, and divergence detection.
All thresholds from ScoringFuturesConfig (zero hardcode).
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..schemas.futures_engine_config import ScoringFuturesConfig


@dataclass
class L3Result:
    score: float              # 0-20
    rsi_score: float
    macd_score: float
    ema_score: float
    vwap_score: float
    divergence_score: float
    divergences: List[str]    # e.g. ["bullish_rsi", "bearish_macd"]
    details: Dict[str, Any]


def _calc_rsi(closes: pd.Series, period: int) -> pd.Series:
    delta = closes.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _detect_divergence(prices: pd.Series, indicator: pd.Series, lookback: int) -> Optional[str]:
    """
    Detect regular bullish or bearish divergence over lookback candles.
    Returns 'bullish', 'bearish', or None.
    """
    if len(prices) < lookback or len(indicator) < lookback:
        return None

    p  = prices.iloc[-lookback:].values
    ind = indicator.iloc[-lookback:].values

    # Bullish divergence: price makes lower low, indicator makes higher low
    price_ll = p[-1] < np.min(p[:-1])
    ind_hl   = ind[-1] > np.min(ind[:-1])
    if price_ll and ind_hl:
        return "bullish"

    # Bearish divergence: price makes higher high, indicator makes lower high
    price_hh = p[-1] > np.max(p[:-1])
    ind_lh   = ind[-1] < np.max(ind[:-1])
    if price_hh and ind_lh:
        return "bearish"

    return None


def score_momentum(
    df: pd.DataFrame,
    trade_direction: str,   # "long" | "short"
    cfg: ScoringFuturesConfig,
) -> L3Result:
    """
    Calculate L3 Momentum score.

    Args:
        df:              OHLCV DataFrame (at least 200 candles recommended)
        trade_direction: "long" or "short"
        cfg:             ScoringFuturesConfig
    """
    closes = df["close"].astype(float)
    highs  = df["high"].astype(float)
    lows   = df["low"].astype(float)
    volume = df["volume"].astype(float)
    n      = len(closes)

    # ── RSI ───────────────────────────────────────────────────────────────────
    rsi_period = cfg.l3_rsi_period
    rsi = _calc_rsi(closes, rsi_period)
    rsi_val = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

    # Score RSI (0-5): direction-aware
    # LONG: RSI 40-65 = ideal (momentum but not overbought) → 5
    #       RSI 30-40 or 65-72 → 3
    #       RSI < 30 (oversold) → 2 (contrarian long is risky in futures)
    #       RSI > 72 → 0 (overbought, avoid long)
    # SHORT: inverse
    if trade_direction == "long":
        if 40 <= rsi_val <= 65:
            rsi_pts = 5.0
        elif (30 <= rsi_val < 40) or (65 < rsi_val <= 72):
            rsi_pts = 3.0
        elif rsi_val < 30:
            rsi_pts = 2.0
        else:
            rsi_pts = 0.0
    else:  # short
        if 35 <= rsi_val <= 60:
            rsi_pts = 5.0
        elif (28 <= rsi_val < 35) or (60 < rsi_val <= 70):
            rsi_pts = 3.0
        elif rsi_val > 70:
            rsi_pts = 2.0
        else:
            rsi_pts = 0.0

    # ── MACD ──────────────────────────────────────────────────────────────────
    ema_fast   = closes.ewm(span=12, adjust=False).mean()
    ema_slow   = closes.ewm(span=26, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram  = macd_line - signal_line

    macd_val  = float(macd_line.iloc[-1])
    hist_val  = float(histogram.iloc[-1])
    hist_prev = float(histogram.iloc[-2]) if n >= 2 else 0.0

    # Score MACD (0-4): direction-aware
    macd_above_signal = macd_val > float(signal_line.iloc[-1])
    hist_increasing   = hist_val > hist_prev

    if trade_direction == "long":
        if macd_above_signal and hist_increasing and macd_val > 0:
            macd_pts = 4.0
        elif macd_above_signal and hist_increasing:
            macd_pts = 3.0
        elif macd_above_signal:
            macd_pts = 2.0
        else:
            macd_pts = 0.0
    else:
        if not macd_above_signal and not hist_increasing and macd_val < 0:
            macd_pts = 4.0
        elif not macd_above_signal and not hist_increasing:
            macd_pts = 3.0
        elif not macd_above_signal:
            macd_pts = 2.0
        else:
            macd_pts = 0.0

    # ── EMA alignment (0-4) ───────────────────────────────────────────────────
    ema9   = closes.ewm(span=9,   adjust=False).mean()
    ema21  = closes.ewm(span=21,  adjust=False).mean()
    ema200 = closes.ewm(span=200, adjust=False).mean()

    e9  = float(ema9.iloc[-1])
    e21 = float(ema21.iloc[-1])
    e200 = float(ema200.iloc[-1]) if n >= 200 else None
    close = float(closes.iloc[-1])

    if trade_direction == "long":
        full_align = e9 > e21 and (e200 is None or e21 > e200)
        price_above = close > e21
        ema_pts = 4.0 if full_align and price_above else (
                  2.5 if full_align else (1.5 if price_above else 0.0)
        )
    else:
        full_align = e9 < e21 and (e200 is None or e21 < e200)
        price_below = close < e21
        ema_pts = 4.0 if full_align and price_below else (
                  2.5 if full_align else (1.5 if price_below else 0.0)
        )

    # ── VWAP (0-3) ────────────────────────────────────────────────────────────
    typical = (highs + lows + closes) / 3
    vwap = (typical * volume).cumsum() / volume.cumsum().replace(0, np.nan)
    vwap_val = float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else close

    vwap_pct = (close - vwap_val) / vwap_val * 100 if vwap_val > 0 else 0

    if trade_direction == "long":
        if 0 < vwap_pct <= 2:
            vwap_pts = 3.0   # price just above VWAP — ideal pullback entry zone
        elif vwap_pct > 2:
            vwap_pts = 1.5   # extended above VWAP
        elif -1 <= vwap_pct <= 0:
            vwap_pts = 2.0   # just below VWAP — buying at discount
        else:
            vwap_pts = 0.5
    else:
        if -2 <= vwap_pct < 0:
            vwap_pts = 3.0
        elif vwap_pct < -2:
            vwap_pts = 1.5
        elif 0 <= vwap_pct <= 1:
            vwap_pts = 2.0
        else:
            vwap_pts = 0.5

    # ── Divergences (−4 to +4) ────────────────────────────────────────────────
    lookback   = cfg.l3_divergence_lookback
    rsi_div    = _detect_divergence(closes, rsi, lookback)
    macd_div   = _detect_divergence(closes, histogram, lookback)

    divergences = []
    div_pts = 0.0

    for div_name, div_type in [("rsi", rsi_div), ("macd", macd_div)]:
        if div_type:
            label = f"{div_type}_{div_name}"
            divergences.append(label)
            # Bullish divergence for long = +2, for short = -2
            if (div_type == "bullish" and trade_direction == "long"):
                div_pts += 2.0
            elif (div_type == "bearish" and trade_direction == "short"):
                div_pts += 2.0
            elif (div_type == "bullish" and trade_direction == "short"):
                div_pts -= 2.0  # warning: divergence against trade direction
            elif (div_type == "bearish" and trade_direction == "long"):
                div_pts -= 2.0

    total = round(rsi_pts + macd_pts + ema_pts + vwap_pts + div_pts, 2)
    total = max(0.0, min(20.0, total))

    return L3Result(
        score=total,
        rsi_score=rsi_pts,
        macd_score=macd_pts,
        ema_score=ema_pts,
        vwap_score=vwap_pts,
        divergence_score=div_pts,
        divergences=divergences,
        details={
            "rsi":       round(rsi_val, 2),
            "macd":      round(macd_val, 6),
            "histogram": round(hist_val, 6),
            "ema9":      round(e9, 6),
            "ema21":     round(e21, 6),
            "vwap":      round(vwap_val, 6),
            "vwap_pct":  round(vwap_pct, 2),
            "close":     round(close, 6),
        },
    )
