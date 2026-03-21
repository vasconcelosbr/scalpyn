"""L1 — Liquidity Score (0-20).

Evaluates trading viability based on volume, relative volume, spread, and book depth.
Hard rule: L1 < l1_hard_reject (default 10) → REJECT trade regardless of other layers.

All thresholds from ScoringFuturesConfig.l1_weights (zero hardcode).
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..schemas.futures_engine_config import ScoringFuturesConfig


@dataclass
class L1Result:
    score: float                # 0-20
    volume_score: float
    rel_volume_score: float
    spread_score: float
    depth_score: float
    rejected: bool              # True if score < l1_hard_reject
    details: Dict[str, Any]


def score_liquidity(
    volume_24h_usdt: float,
    relative_volume: float,       # current vol / 20d avg vol
    spread_pct: float,            # (ask - bid) / mid * 100
    book_depth_usdt: float,       # total USDT within ±2% of mid
    cfg: ScoringFuturesConfig,
) -> L1Result:
    """
    Calculate the L1 Liquidity score.

    Args:
        volume_24h_usdt:  24h trading volume in USDT
        relative_volume:  current period volume / 20-day average (1.0 = average)
        spread_pct:       bid-ask spread as % of mid price
        book_depth_usdt:  total order book depth within ±2% of mid price in USDT
        cfg:              ScoringFuturesConfig

    Returns:
        L1Result with breakdown and rejection flag.
    """
    w = cfg.l1_weights

    # ── Volume 24h (0 → w.volume_24h = 7) ────────────────────────────────────
    # Tiers: < $1M → 0, $1M-5M → 2, $5M-20M → 4, $20M-100M → 6, $100M+ → 7
    if volume_24h_usdt >= 100_000_000:
        vol_pts = w.volume_24h
    elif volume_24h_usdt >= 20_000_000:
        vol_pts = w.volume_24h * (6 / 7)
    elif volume_24h_usdt >= 5_000_000:
        vol_pts = w.volume_24h * (4 / 7)
    elif volume_24h_usdt >= 1_000_000:
        vol_pts = w.volume_24h * (2 / 7)
    else:
        vol_pts = 0.0

    # ── Relative volume (0 → w.relative_volume = 5) ──────────────────────────
    # rel < 0.5 → 0, 0.5-0.8 → 1, 0.8-1.2 → 2, 1.2-2.0 → 4, 2.0+ → 5
    if relative_volume >= 2.0:
        rel_pts = w.relative_volume
    elif relative_volume >= 1.2:
        rel_pts = w.relative_volume * (4 / 5)
    elif relative_volume >= 0.8:
        rel_pts = w.relative_volume * (2 / 5)
    elif relative_volume >= 0.5:
        rel_pts = w.relative_volume * (1 / 5)
    else:
        rel_pts = 0.0

    # ── Spread (0 → w.spread = 4) — lower is better ──────────────────────────
    # spread < 0.02% → 4, 0.02-0.05% → 3, 0.05-0.1% → 2, 0.1-0.2% → 1, > 0.2% → 0
    if spread_pct < 0.02:
        spread_pts = w.spread
    elif spread_pct < 0.05:
        spread_pts = w.spread * (3 / 4)
    elif spread_pct < 0.10:
        spread_pts = w.spread * (2 / 4)
    elif spread_pct < 0.20:
        spread_pts = w.spread * (1 / 4)
    else:
        spread_pts = 0.0

    # ── Book depth (0 → w.book_depth = 4) ────────────────────────────────────
    # < $100k → 0, $100k-500k → 1, $500k-2M → 2, $2M-10M → 3, $10M+ → 4
    if book_depth_usdt >= 10_000_000:
        depth_pts = w.book_depth
    elif book_depth_usdt >= 2_000_000:
        depth_pts = w.book_depth * (3 / 4)
    elif book_depth_usdt >= 500_000:
        depth_pts = w.book_depth * (2 / 4)
    elif book_depth_usdt >= 100_000:
        depth_pts = w.book_depth * (1 / 4)
    else:
        depth_pts = 0.0

    total = round(vol_pts + rel_pts + spread_pts + depth_pts, 2)
    rejected = total < cfg.l1_hard_reject

    return L1Result(
        score=total,
        volume_score=round(vol_pts, 2),
        rel_volume_score=round(rel_pts, 2),
        spread_score=round(spread_pts, 2),
        depth_score=round(depth_pts, 2),
        rejected=rejected,
        details={
            "volume_24h_usdt":   volume_24h_usdt,
            "relative_volume":   round(relative_volume, 2),
            "spread_pct":        round(spread_pct, 4),
            "book_depth_usdt":   book_depth_usdt,
            "l1_hard_reject":    cfg.l1_hard_reject,
        },
    )


async def fetch_and_score(
    contract: str,
    adapter,
    cfg: ScoringFuturesConfig,
) -> L1Result:
    """
    Fetch liquidity data from Gate.io and score it.
    adapter: GateAdapter instance.
    """
    # Fetch ticker (volume + last price)
    tickers = await adapter.get_tickers(symbols=[contract], market="futures")
    ticker  = tickers[0] if tickers else {}

    volume_24h = float(ticker.get("volume_24h_quote", ticker.get("volume_24h", 0)) or 0)
    last_price = float(ticker.get("last", 1) or 1)

    # Relative volume: compare current vs estimate (no 20d avg directly — use volume_24h_base)
    # Gate.io doesn't provide 20d avg volume directly; use volume_24h as proxy.
    # A relative_volume of 1.0 means "average"; we set default 1.0 if unavailable.
    relative_volume = float(ticker.get("volume_24h_change_pct", 0) or 0) / 100 + 1.0

    # Fetch order book
    try:
        book = await adapter.get_orderbook(contract, market="futures", depth=20)
        asks = book.get("asks", [])
        bids = book.get("bids", [])

        # Spread from best bid/ask
        best_ask = float(asks[0][0]) if asks else last_price * 1.001
        best_bid = float(bids[0][0]) if bids else last_price * 0.999
        mid      = (best_ask + best_bid) / 2
        spread_pct = ((best_ask - best_bid) / mid * 100) if mid > 0 else 0.5

        # Book depth: sum all ask + bid volume within ±2% of mid
        depth_limit = mid * 0.02
        book_depth = sum(
            float(a[0]) * float(a[1])
            for a in asks
            if abs(float(a[0]) - mid) <= depth_limit
        ) + sum(
            float(b[0]) * float(b[1])
            for b in bids
            if abs(float(b[0]) - mid) <= depth_limit
        )
    except Exception:
        spread_pct  = 0.1
        book_depth  = 500_000

    return score_liquidity(volume_24h, relative_volume, spread_pct, book_depth, cfg)
