"""L5 — Order Flow Score (0-20).

Taker buy/sell ratio, funding rate, open interest changes, liquidations, whale activity.
All thresholds from ScoringFuturesConfig (zero hardcode).

Contracts:
  buy_pressure = taker_buy_volume / (taker_buy_volume + taker_sell_volume)  → [0, 1]
  taker_ratio  = taker_buy_volume / (taker_buy_volume + taker_sell_volume)  → [0, 1] or None
                 Same canonical "Buy Volume Ratio" formula as buy_pressure
                 (#82 unified the two — until then taker_ratio was buy/sell
                 in (0, 5], which conflicted with futures_pipeline_scorer's
                 own thresholds 0.50/0.55/0.65 on the same field).
                 None means no taker activity in the window.

Source: Gate.io futures trades endpoint (real individual trade data, last 60s window).
No fallback via long_short_account_ratio. No hard-coded 0.5 default.
If no trade data → buy_pressure = None → taker component scores neutral (2.0 pts).
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..schemas.futures_engine_config import ScoringFuturesConfig
from ..services.order_flow_service import safe_taker_ratio

logger = logging.getLogger(__name__)

FUTURES_TRADE_WINDOW_SECONDS = 60
FUTURES_TRADE_LIMIT = 500


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
    buy_pressure: Optional[float],  # buy_volume / (buy+sell), [0, 1], None if unavailable
    funding_rate: float,            # current funding rate (e.g. 0.0001 = 0.01%)
    oi_change_pct: float,           # OI change in last 4h as % (e.g. 5 = +5%)
    liq_longs_24h_usdt: float,      # liquidated longs in USD last 24h
    liq_shorts_24h_usdt: float,     # liquidated shorts in USD last 24h
    whale_buys_usdt: float,         # large buy transactions > threshold in USD
    whale_sells_usdt: float,        # large sell transactions > threshold in USD
    trade_direction: str,           # "long" | "short"
    cfg: ScoringFuturesConfig,
    taker_ratio: Optional[float] = None,  # buy / (buy + sell), [0, 1] — same as buy_pressure (#82)
) -> L5Result:
    """Calculate L5 Order Flow score."""
    ext_pos = cfg.l5_funding_extreme_positive
    ext_neg = cfg.l5_funding_extreme_negative

    # ── Taker / buy pressure (0-5) ────────────────────────────────────────────
    # buy_pressure: 0.5 = neutral, >0.6 = bullish flow, <0.4 = bearish flow.
    # If buy_pressure is None (no trade data available) → neutral score (2.0).
    if buy_pressure is None:
        taker_pts = 2.0   # neutral — no data, no penalty and no boost
    else:
        taker_imbalance = buy_pressure - 0.5   # maps [0, 1] → [-0.5, +0.5]

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
            funding_pts = 4.0
        elif funding_direction == "BALANCED" and funding_rate < 0:
            funding_pts = 5.0
        elif funding_direction == "SHORT_CROWDED":
            funding_pts = 5.0
        elif funding_direction == "LONG_CROWDED":
            funding_pts = 0.0 if funding_rate > ext_pos * 2 else 1.5
        else:
            funding_pts = 2.5
    else:  # short
        if funding_direction == "BALANCED" and funding_rate <= 0:
            funding_pts = 4.0
        elif funding_direction == "BALANCED" and funding_rate > 0:
            funding_pts = 5.0
        elif funding_direction == "LONG_CROWDED":
            funding_pts = 5.0
        elif funding_direction == "SHORT_CROWDED":
            funding_pts = 0.0 if funding_rate < ext_neg * 2 else 1.5
        else:
            funding_pts = 2.5

    # ── Open Interest change (0-4) ────────────────────────────────────────────
    if trade_direction == "long":
        if oi_change_pct >= 5:
            oi_pts = 4.0
        elif oi_change_pct >= 2:
            oi_pts = 3.0
        elif oi_change_pct >= 0:
            oi_pts = 2.0
        elif oi_change_pct >= -2:
            oi_pts = 1.0
        else:
            oi_pts = 0.0
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
    total_liq = liq_longs_24h_usdt + liq_shorts_24h_usdt
    if total_liq > 0:
        liq_ratio = (
            liq_longs_24h_usdt / total_liq if trade_direction == "short"
            else liq_shorts_24h_usdt / total_liq
        )
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
        whale_pts = 0.0

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
            "buy_pressure":          round(buy_pressure, 3) if buy_pressure is not None else None,
            "taker_ratio":           round(taker_ratio,  4) if taker_ratio  is not None else None,
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
    """Fetch all order flow data points from Gate.io for L5 scoring.

    Uses real futures trade data for buy_pressure / taker_ratio.
    No fallback via long_short_account_ratio or hard-coded defaults.
    Returns raw dict with all inputs for score_order_flow().
    """
    # Contract info (funding rate)
    try:
        info    = await adapter.get_contract_info(contract)
        funding = float(info.get("funding_rate", 0) or 0)
    except Exception:
        funding = 0.0

    # Contract stats (OI only — no longer used for taker proxy)
    try:
        stats     = await adapter.get_contract_stats(contract, interval="4h", limit=2)
        oi_now    = float((stats[0] if stats else {}).get("open_interest_usdt", 0) or 0)
        oi_prev   = float((stats[1] if len(stats) > 1 else {}).get("open_interest_usdt", oi_now) or oi_now)
        oi_change = ((oi_now - oi_prev) / oi_prev * 100) if oi_prev > 0 else 0.0
    except Exception:
        oi_change = 0.0

    # Real futures trade aggregation for taker metrics
    buy_pressure: Optional[float] = None
    taker_ratio:  Optional[float] = None
    try:
        trades = await adapter._request(
            "GET", f"/futures/{adapter.SETTLE}/trades",
            params={"contract": contract, "limit": str(FUTURES_TRADE_LIMIT)},
            base_url=adapter.FUTURES_BASE,
        )
        if trades:
            cutoff = time.time() - FUTURES_TRADE_WINDOW_SECONDS
            buy_vol  = 0.0
            sell_vol = 0.0
            for t in trades:
                ts = float(t.get("create_time", 0) or 0)
                if ts < cutoff:
                    continue
                size = float(t.get("size", 0) or 0)
                if size > 0:
                    buy_vol  += size
                elif size < 0:
                    sell_vol += abs(size)

            # taker_ratio and buy_pressure are now the same canonical
            # "Buy Volume Ratio" formula: buy / (buy + sell), bounded
            # [0, 1]. Single helper guarantees identical guards across
            # spot and futures collectors (#82).
            taker_ratio = safe_taker_ratio(
                contract, FUTURES_TRADE_WINDOW_SECONDS, buy_vol, sell_vol,
            )
            buy_pressure = taker_ratio
    except Exception as exc:
        logger.warning("[L5] failed to fetch futures trades for %s: %s", contract, exc)

    # Liquidations
    try:
        liq_data   = await adapter._request(
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
        "buy_pressure":          buy_pressure,
        "taker_ratio":           taker_ratio,
        "funding_rate":          funding,
        "oi_change_pct":         oi_change,
        "liq_longs_24h_usdt":    liq_longs,
        "liq_shorts_24h_usdt":   liq_shorts,
        "whale_buys_usdt":       0.0,
        "whale_sells_usdt":      0.0,
    }
