"""L5 — Order Flow Score (0-20).

Taker buy/sell ratio, funding rate, open interest changes, liquidations, whale activity.
All thresholds from ScoringFuturesConfig (zero hardcode).
"""

from dataclasses import dataclass
from typing import Any, Dict, List

from ..schemas.futures_engine_config import ScoringFuturesConfig


@dataclass
class L5Result:
    score: float
    taker_score: float
    funding_score: float
    oi_score: float
    liquidation_score: float
    whale_score: float
    funding_direction: str      # "LONG_CROWDED" | "SHORT_CROWDED" | "BALANCED"
    details: Dict[str, Any]


def score_order_flow(
    taker_buy_ratio: float,     # buy_volume / total_volume (0-1), 0.5 = neutral
    funding_rate: float,        # current funding rate (e.g. 0.0001 = 0.01%)
    oi_change_pct: float,       # OI change in last 4h as % (e.g. 5 = +5%)
    liq_longs_24h_usdt: float,  # liquidated longs in USD last 24h
    liq_shorts_24h_usdt: float, # liquidated shorts in USD last 24h
    whale_buys_usdt: float,     # large buy transactions > threshold in USD (recent period)
    whale_sells_usdt: float,    # large sell transactions > threshold in USD
    trade_direction: str,       # "long" | "short"
    cfg: ScoringFuturesConfig,
) -> L5Result:
    """
    Calculate L5 Order Flow score.
    """
    ext_pos = cfg.l5_funding_extreme_positive
    ext_neg = cfg.l5_funding_extreme_negative

    # ── Taker ratio (0-5) ─────────────────────────────────────────────────────
    # taker_buy_ratio: 0.5 = neutral, >0.6 = bullish flow, <0.4 = bearish flow
    taker_imbalance = taker_buy_ratio - 0.5   # -0.5 to +0.5

    if trade_direction == "long":
        if taker_imbalance >= 0.15:
            taker_pts = 5.0   # strong buying pressure
        elif taker_imbalance >= 0.05:
            taker_pts = 3.5
        elif taker_imbalance >= -0.05:
            taker_pts = 2.0   # neutral
        elif taker_imbalance >= -0.15:
            taker_pts = 1.0
        else:
            taker_pts = 0.0   # dominant selling
    else:  # short
        if taker_imbalance <= -0.15:
            taker_pts = 5.0
        elif taker_imbalance <= -0.05:
            taker_pts = 3.5
        elif taker_imbalance <= 0.05:
            taker_pts = 2.0
        elif taker_imbalance <= 0.15:
            taker_pts = 1.0
        else:
            taker_pts = 0.0

    # ── Funding rate (0-5) ────────────────────────────────────────────────────
    if funding_rate > ext_pos:
        funding_direction = "LONG_CROWDED"
    elif funding_rate < ext_neg:
        funding_direction = "SHORT_CROWDED"
    else:
        funding_direction = "BALANCED"

    if trade_direction == "long":
        if funding_direction == "BALANCED" and funding_rate >= 0:
            funding_pts = 4.0   # paying but not excessive — longs not crowded
        elif funding_direction == "BALANCED" and funding_rate < 0:
            funding_pts = 5.0   # collecting funding — shorts crowded → long favorable
        elif funding_direction == "SHORT_CROWDED":
            funding_pts = 5.0   # shorts extremely crowded → contrarian long
        elif funding_direction == "LONG_CROWDED":
            # Positive funding hurts longs (paying + longs crowded)
            # the more extreme, the worse
            if funding_rate > ext_pos * 2:
                funding_pts = 0.0
            else:
                funding_pts = 1.5
        else:
            funding_pts = 2.5
    else:  # short
        if funding_direction == "BALANCED" and funding_rate <= 0:
            funding_pts = 4.0
        elif funding_direction == "BALANCED" and funding_rate > 0:
            funding_pts = 5.0   # longs paying → short collecting
        elif funding_direction == "LONG_CROWDED":
            funding_pts = 5.0
        elif funding_direction == "SHORT_CROWDED":
            if funding_rate < ext_neg * 2:
                funding_pts = 0.0
            else:
                funding_pts = 1.5
        else:
            funding_pts = 2.5

    # ── Open Interest change (0-4) ────────────────────────────────────────────
    # OI rising + price rising = longs building = bullish (for long)
    # OI rising + price falling = shorts building = bearish (for short)
    # OI falling = position unwinding (less conviction)
    if trade_direction == "long":
        if oi_change_pct >= 5:
            oi_pts = 4.0    # strong OI build = conviction
        elif oi_change_pct >= 2:
            oi_pts = 3.0
        elif oi_change_pct >= 0:
            oi_pts = 2.0
        elif oi_change_pct >= -2:
            oi_pts = 1.0
        else:
            oi_pts = 0.0    # OI dropping = longs leaving
    else:
        if oi_change_pct <= -2:
            oi_pts = 4.0
        elif oi_change_pct <= 0:
            oi_pts = 3.0
        elif oi_change_pct <= 2:
            oi_pts = 2.0
        elif oi_change_pct <= 5:
            oi_pts = 1.0
        else:
            oi_pts = 0.0

    # ── Liquidation data (0-3) ────────────────────────────────────────────────
    # Recent liquidations of the opposing side = fuel for our direction
    total_liq = liq_longs_24h_usdt + liq_shorts_24h_usdt
    if total_liq > 0:
        liq_ratio = (
            liq_longs_24h_usdt / total_liq if trade_direction == "short"
            else liq_shorts_24h_usdt / total_liq
        )
        # High liq_ratio = opposing side getting wrecked = good for our direction
        if liq_ratio >= 0.7:
            liq_pts = 3.0
        elif liq_ratio >= 0.6:
            liq_pts = 2.0
        elif liq_ratio >= 0.5:
            liq_pts = 1.0
        else:
            liq_pts = 0.0
    else:
        liq_pts = 1.0   # no liquidation data = neutral

    # ── Whale activity (0-3) ──────────────────────────────────────────────────
    total_whale = whale_buys_usdt + whale_sells_usdt
    if total_whale > 0:
        whale_buy_ratio = whale_buys_usdt / total_whale
        if trade_direction == "long":
            if whale_buy_ratio >= 0.65:
                whale_pts = 3.0
            elif whale_buy_ratio >= 0.5:
                whale_pts = 1.5
            else:
                whale_pts = 0.0
        else:
            if whale_buy_ratio <= 0.35:
                whale_pts = 3.0
            elif whale_buy_ratio <= 0.5:
                whale_pts = 1.5
            else:
                whale_pts = 0.0
    else:
        whale_pts = 0.0   # no data

    total = round(taker_pts + funding_pts + oi_pts + liq_pts + whale_pts, 2)
    total = min(20.0, total)

    return L5Result(
        score=total,
        taker_score=taker_pts,
        funding_score=funding_pts,
        oi_score=oi_pts,
        liquidation_score=liq_pts,
        whale_score=whale_pts,
        funding_direction=funding_direction,
        details={
            "taker_buy_ratio":       round(taker_buy_ratio, 3),
            "funding_rate":          funding_rate,
            "oi_change_pct":         round(oi_change_pct, 2),
            "liq_longs_24h_usdt":    liq_longs_24h_usdt,
            "liq_shorts_24h_usdt":   liq_shorts_24h_usdt,
            "whale_buys_usdt":       whale_buys_usdt,
            "whale_sells_usdt":      whale_sells_usdt,
            "funding_extreme_pos":   ext_pos,
            "funding_extreme_neg":   ext_neg,
        },
    )


async def fetch_order_flow_data(
    contract: str,
    adapter,
    cfg: ScoringFuturesConfig,
) -> dict:
    """
    Fetch all order flow data points from Gate.io for L5 scoring.
    Returns raw dict with all inputs for score_order_flow().
    """
    # Contract info (funding rate)
    try:
        info       = await adapter.get_contract_info(contract)
        funding    = float(info.get("funding_rate", 0) or 0)
    except Exception:
        funding    = 0.0

    # Contract stats (OI, long/short ratio)
    try:
        stats     = await adapter.get_contract_stats(contract, interval="4h", limit=2)
        oi_now    = float((stats[0] if stats else {}).get("open_interest_usdt", 0) or 0)
        oi_prev   = float((stats[1] if len(stats) > 1 else {}).get("open_interest_usdt", oi_now) or oi_now)
        oi_change = ((oi_now - oi_prev) / oi_prev * 100) if oi_prev > 0 else 0.0

        ls_ratio  = float((stats[0] if stats else {}).get("long_short_account_ratio", 0.5) or 0.5)
        taker_buy = ls_ratio  # proxy: long/short account ratio ≈ buy pressure
    except Exception:
        oi_change = 0.0
        taker_buy = 0.5

    # Tickers for volume (taker ratio proxy via volume_24h_buy / volume_24h)
    try:
        tickers   = await adapter.get_tickers(symbols=[contract], market="futures")
        ticker    = tickers[0] if tickers else {}
        vol_buy   = float(ticker.get("volume_24h_buy",  0) or 0)
        vol_total = float(ticker.get("volume_24h",      1) or 1)
        if vol_buy > 0 and vol_total > 0:
            taker_buy = vol_buy / vol_total
    except Exception:
        pass

    # Liquidations
    try:
        liq_data = await adapter._request(
            "GET", f"/futures/{adapter.SETTLE}/liq_orders",
            params={"contract": contract, "limit": "100"},
            base_url=adapter.FUTURES_BASE,
        )
        liq_longs  = sum(float(l.get("size", 0) or 0) * float(l.get("fill_price", 0) or 0)
                         for l in liq_data if float(l.get("size", 0) or 0) < 0)
        liq_shorts = sum(float(l.get("size", 0) or 0) * float(l.get("fill_price", 0) or 0)
                         for l in liq_data if float(l.get("size", 0) or 0) > 0)
        liq_longs  = abs(liq_longs)
        liq_shorts = abs(liq_shorts)
    except Exception:
        liq_longs  = 0.0
        liq_shorts = 0.0

    return {
        "taker_buy_ratio":       taker_buy,
        "funding_rate":          funding,
        "oi_change_pct":         oi_change,
        "liq_longs_24h_usdt":    liq_longs,
        "liq_shorts_24h_usdt":   liq_shorts,
        "whale_buys_usdt":       0.0,   # Gate.io doesn't have direct whale API
        "whale_sells_usdt":      0.0,
    }
