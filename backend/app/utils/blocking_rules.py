"""Anti-bad-entry blocking rules — shared between pipeline_scan and watchlists.

These rules prevent assets with poor market microstructure from entering or
remaining in any watchlist level (L1/L2/L3).  Blocked assets are **removed**
from watchlists entirely rather than being kept with a ``blocked=True`` flag.

Thresholds are intentionally hardcoded here so that the same values are used
consistently across the Celery pipeline scan and the API manual-refresh path.
"""

from typing import Dict, List, Tuple

# ── Hardcoded thresholds ──────────────────────────────────────────────────────
_MAX_SPREAD_PCT = 1.5
_MIN_DEPTH_USDT = 5_000
_MAX_RSI = 75
_MIN_TAKER_RATIO = 0.4


def check_anti_bad_entry(asset: Dict) -> Tuple[bool, List[str]]:
    """Evaluate anti-bad-entry blocking rules for an asset.

    Parameters
    ----------
    asset : dict
        Asset data dict.  Expected keys (all optional):
        ``spread_pct``, ``orderbook_depth_usdt``, ``rsi``, ``taker_ratio``.
        Indicator values may live at the top level or inside an ``indicators``
        sub-dict.

    Returns
    -------
    tuple[bool, list[str]]
        ``(blocked, reasons)`` — *blocked* is ``True`` when **any** rule
        triggers; *reasons* lists human-readable descriptions.
    """
    block_reasons: List[str] = []

    # Helper: look up a value in the top-level dict first, then fall back to
    # the nested ``indicators`` dict (pipeline_scan stores flat, API stores nested).
    def _val(key: str):
        v = asset.get(key)
        if v is None:
            v = (asset.get("indicators") or {}).get(key)
        return v

    spread = _val("spread_pct")
    depth = _val("orderbook_depth_usdt")
    rsi = _val("rsi")
    taker = _val("taker_ratio")

    if spread is not None:
        try:
            if float(spread) > _MAX_SPREAD_PCT:
                block_reasons.append(f"spread>{_MAX_SPREAD_PCT}%")
        except (TypeError, ValueError):
            pass

    if depth is not None:
        try:
            if float(depth) < _MIN_DEPTH_USDT:
                block_reasons.append(f"depth<{_MIN_DEPTH_USDT / 1000:.0f}k")
        except (TypeError, ValueError):
            pass

    if rsi is not None:
        try:
            if float(rsi) > _MAX_RSI:
                block_reasons.append(f"rsi>{_MAX_RSI}")
        except (TypeError, ValueError):
            pass

    if taker is not None:
        try:
            if float(taker) < _MIN_TAKER_RATIO:
                block_reasons.append(f"taker<{_MIN_TAKER_RATIO}")
        except (TypeError, ValueError):
            pass

    return (len(block_reasons) > 0, block_reasons)


def is_blocked(asset: Dict) -> bool:
    """Shorthand — return True when any anti-bad-entry rule triggers."""
    blocked, _ = check_anti_bad_entry(asset)
    return blocked
