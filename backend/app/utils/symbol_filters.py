"""Utility functions for filtering exchange symbols."""

import re

_LEVERAGED_RE = re.compile(
    r"""
    (
        \d+[LS]_USDT$        # e.g. BTC5S_USDT, DOGE3L_USDT, SOL5L_USDT
      | (UP|DOWN|BULL|BEAR)_USDT$   # e.g. BTCUP_USDT, BTCDOWN_USDT
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_leveraged_token(symbol: str) -> bool:
    """Return True if the symbol is a leveraged/inverse token, not a real crypto."""
    return bool(_LEVERAGED_RE.search(symbol))


def filter_real_assets(symbols: list[str]) -> list[str]:
    """Remove leveraged tokens from a list of symbols."""
    return [s for s in symbols if not is_leveraged_token(s)]
