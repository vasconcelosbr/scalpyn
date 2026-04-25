"""Futures Pipeline Scorer — dual independent LONG/SHORT scoring.

Scoring layers (max 100 pts per direction):
  L1 Liquidity   (shared,          max  20 pts) — volume_spike, spread_pct, orderbook_depth
  L2 Structure   (direction-aware, max  25 pts) — EMA alignment (bullish vs bearish)
  L3 Momentum    (direction-aware, max  30 pts) — RSI, MACD histogram, stoch_k
  L4 Volatility  (shared,          max  10 pts) — atr_percent, bb_width
  L5 Order Flow  (direction-aware, max  15 pts) — taker_ratio

LONG and SHORT are scored independently — SHORT is NOT an inversion of LONG.
Entry gate is a hard gate, completely independent of score values.
Direction is only resolved at L3 level (requires a meaningful gap >= 5 pts).
"""

from typing import Any, Dict, Optional


_KEY_INDICATORS = ("rsi", "macd_histogram", "stoch_k", "ema9_gt_ema50", "ema50_gt_ema200")


def _get(ind: Dict[str, Any], key: str, default=None):
    v = ind.get(key)
    return default if v is None else v


def _has_sufficient_data(ind: Dict[str, Any]) -> bool:
    return any(_get(ind, k) is not None for k in _KEY_INDICATORS)


# ─── L1 Liquidity (shared) ───────────────────────────────────────────────────

def _score_liquidity(ind: Dict[str, Any]) -> float:
    pts = 0.0

    volume_spike = _get(ind, "volume_spike")
    if volume_spike is not None:
        if volume_spike >= 2.0:
            pts += 10.0
        elif volume_spike >= 1.5:
            pts += 7.0
        elif volume_spike >= 1.2:
            pts += 4.0

    spread_pct = _get(ind, "spread_pct")
    if spread_pct is not None:
        if spread_pct <= 0.3:
            pts += 6.0
        elif spread_pct <= 0.8:
            pts += 4.0
        elif spread_pct <= 1.5:
            pts += 2.0

    depth = _get(ind, "orderbook_depth_usdt")
    if depth is not None:
        if depth >= 20_000:
            pts += 4.0
        elif depth >= 5_000:
            pts += 2.0
        elif depth >= 1_000:
            pts += 1.0

    return min(pts, 20.0)


# ─── L2 Structure (direction-aware) ──────────────────────────────────────────

def _score_structure_long(ind: Dict[str, Any]) -> float:
    pts = 0.0
    ema_full   = _get(ind, "ema_full_alignment")
    ema9_gt_50 = _get(ind, "ema9_gt_ema50")
    ema50_gt_200 = _get(ind, "ema50_gt_ema200")

    if ema_full is True:
        pts += 15.0
    elif ema9_gt_50 is True:
        pts += 6.0

    if ema50_gt_200 is True:
        pts += 10.0

    return min(pts, 25.0)


def _score_structure_short(ind: Dict[str, Any]) -> float:
    pts = 0.0
    ema_full   = _get(ind, "ema_full_alignment")
    ema9_gt_50 = _get(ind, "ema9_gt_ema50")
    ema50_gt_200 = _get(ind, "ema50_gt_ema200")

    if ema_full is False and ema9_gt_50 is False and ema50_gt_200 is False:
        pts += 15.0
    elif ema9_gt_50 is False:
        pts += 6.0

    if ema50_gt_200 is False:
        pts += 10.0

    return min(pts, 25.0)


# ─── L3 Momentum (direction-aware) ───────────────────────────────────────────

def _score_momentum_long(ind: Dict[str, Any]) -> float:
    pts = 0.0

    rsi = _get(ind, "rsi")
    if rsi is not None:
        rsi = float(rsi)
        if 45.0 <= rsi <= 65.0:
            pts += 12.0
        elif 35.0 <= rsi < 45.0:
            pts += 8.0
        elif 65.0 < rsi <= 75.0:
            pts += 4.0

    macd_hist = _get(ind, "macd_histogram")
    if macd_hist is not None:
        macd_hist = float(macd_hist)
        if macd_hist > 0:
            pts += 10.0
        elif macd_hist >= -0.0001:
            pts += 5.0

    stoch_k = _get(ind, "stoch_k")
    if stoch_k is not None:
        stoch_k = float(stoch_k)
        if 30.0 <= stoch_k <= 70.0:
            pts += 8.0
        elif stoch_k < 30.0:
            pts += 4.0

    return min(pts, 30.0)


def _score_momentum_short(ind: Dict[str, Any]) -> float:
    pts = 0.0

    rsi = _get(ind, "rsi")
    if rsi is not None:
        rsi = float(rsi)
        if 35.0 <= rsi <= 55.0:
            pts += 12.0
        elif 55.0 < rsi <= 65.0:
            pts += 8.0
        elif 25.0 <= rsi < 35.0:
            pts += 4.0

    macd_hist = _get(ind, "macd_histogram")
    if macd_hist is not None:
        macd_hist = float(macd_hist)
        if macd_hist < 0:
            pts += 10.0
        elif macd_hist <= 0.0001:
            pts += 5.0

    stoch_k = _get(ind, "stoch_k")
    if stoch_k is not None:
        stoch_k = float(stoch_k)
        if 30.0 <= stoch_k <= 70.0:
            pts += 8.0
        elif stoch_k > 70.0:
            pts += 4.0

    return min(pts, 30.0)


# ─── L4 Volatility (shared) ──────────────────────────────────────────────────

def _score_volatility(ind: Dict[str, Any]) -> float:
    pts = 0.0

    atr_pct = _get(ind, "atr_percent") or _get(ind, "atr_pct")
    if atr_pct is not None:
        atr_pct = float(atr_pct)
        if 0.5 <= atr_pct <= 3.0:
            pts += 6.0
        elif 3.0 < atr_pct <= 5.0:
            pts += 3.0
        elif 0.3 <= atr_pct < 0.5:
            pts += 2.0

    bb_width = _get(ind, "bb_width")
    if bb_width is not None:
        bb_width = float(bb_width)
        if 0.03 <= bb_width <= 0.15:
            pts += 4.0
        elif bb_width > 0.15:
            pts += 2.0

    return min(pts, 10.0)


# ─── L5 Order Flow (direction-aware) ─────────────────────────────────────────

def _score_order_flow_long(ind: Dict[str, Any]) -> float:
    taker = _get(ind, "taker_ratio")
    if taker is None:
        return 0.0
    taker = float(taker)
    if taker >= 0.65:
        return 15.0
    if taker >= 0.55:
        return 10.0
    if taker >= 0.50:
        return 5.0
    return 0.0


def _score_order_flow_short(ind: Dict[str, Any]) -> float:
    taker = _get(ind, "taker_ratio")
    if taker is None:
        return 0.0
    taker = float(taker)
    if taker <= 0.35:
        return 15.0
    if taker <= 0.45:
        return 10.0
    if taker <= 0.50:
        return 5.0
    return 0.0


# ─── Entry gates (config-parametrized, independent of score) ─────────────────

def _entry_long_blocked_cfg(
    ind: Dict[str, Any],
    adx_min: float,
    rsi_overbought: float,
    taker_long_max: float,
) -> bool:
    adx = _get(ind, "adx")
    if adx is not None and float(adx) < adx_min:
        return True

    rsi = _get(ind, "rsi")
    if rsi is not None and float(rsi) > rsi_overbought:
        return True

    taker = _get(ind, "taker_ratio")
    if taker is not None and float(taker) < taker_long_max:
        return True

    return False


def _entry_short_blocked_cfg(
    ind: Dict[str, Any],
    adx_min: float,
    rsi_oversold: float,
    taker_short_min: float,
) -> bool:
    adx = _get(ind, "adx")
    if adx is not None and float(adx) < adx_min:
        return True

    rsi = _get(ind, "rsi")
    if rsi is not None and float(rsi) < rsi_oversold:
        return True

    taker = _get(ind, "taker_ratio")
    if taker is not None and float(taker) > taker_short_min:
        return True

    return False


# ─── Public API ───────────────────────────────────────────────────────────────

def score_futures(
    ind: Dict[str, Any],
    *,
    watchlist_level: str = "L1",
    scoring_futures: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute dual LONG/SHORT futures scores for a single asset.

    Returns None for score fields when indicator data is insufficient.
    Direction is only resolved at L3; non-L3 levels always get None.
    """
    if not _has_sufficient_data(ind):
        return {
            "score_long":          None,
            "score_short":         None,
            "confidence_score":    None,
            "futures_direction":   None,
            "block_both":          False,
            "entry_long_blocked":  False,
            "entry_short_blocked": False,
            "components":          {},
        }

    cfg = scoring_futures or {}
    direction_gap_min     = float(cfg.get("direction_gap_min",      5.0))
    entry_adx_min         = float(cfg.get("entry_adx_min",         15.0))
    entry_rsi_overbought  = float(cfg.get("entry_rsi_overbought",  80.0))
    entry_rsi_oversold    = float(cfg.get("entry_rsi_oversold",    20.0))
    entry_taker_long_max  = float(cfg.get("entry_taker_long_max",   0.30))
    entry_taker_short_min = float(cfg.get("entry_taker_short_min",  0.70))

    liq = _score_liquidity(ind)
    vol = _score_volatility(ind)

    s_long   = _score_structure_long(ind)
    m_long   = _score_momentum_long(ind)
    of_long  = _score_order_flow_long(ind)

    s_short  = _score_structure_short(ind)
    m_short  = _score_momentum_short(ind)
    of_short = _score_order_flow_short(ind)

    score_long  = round(liq + s_long  + m_long  + vol + of_long,  2)
    score_short = round(liq + s_short + m_short + vol + of_short, 2)
    confidence  = round(max(score_long, score_short), 2)

    # BLOCK: ADX below threshold — no meaningful trend, gates both directions
    adx = _get(ind, "adx")
    block_both = bool(adx is not None and float(adx) < entry_adx_min)

    # ENTRY: direction-specific gates (include BLOCK result)
    long_blocked  = block_both or _entry_long_blocked_cfg(
        ind, entry_adx_min, entry_rsi_overbought, entry_taker_long_max
    )
    short_blocked = block_both or _entry_short_blocked_cfg(
        ind, entry_adx_min, entry_rsi_oversold, entry_taker_short_min
    )

    # Direction: L3-only, requires meaningful gap between scores
    direction: Optional[str] = None
    if watchlist_level == "L3":
        gap = score_long - score_short
        if gap >= direction_gap_min:
            direction = "LONG"
        elif gap <= -direction_gap_min:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

    return {
        "score_long":          score_long,
        "score_short":         score_short,
        "confidence_score":    confidence,
        "futures_direction":   direction,
        "block_both":          block_both,
        "entry_long_blocked":  long_blocked,
        "entry_short_blocked": short_blocked,
        "components": {
            "liquidity":        liq,
            "structure_long":   s_long,
            "structure_short":  s_short,
            "momentum_long":    m_long,
            "momentum_short":   m_short,
            "volatility":       vol,
            "order_flow_long":  of_long,
            "order_flow_short": of_short,
        },
    }
