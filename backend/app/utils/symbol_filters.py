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

# Base-currency regex for leveraged ETF tokens (used by callers that pass
# the base part of a currency pair rather than the full "XXX_USDT" symbol).
_ETF_BASE_RE = re.compile(
    r"""
    (
        \d+[LS]$              # e.g. BTC3L, ETH5S
      | (UP|DOWN|BULL|BEAR)$  # e.g. BTCUP, ETHDOWN
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Stablecoins pegged to fiat — useless for directional trading.
_STABLECOIN_BASES: frozenset[str] = frozenset({
    "USDC", "DAI", "TUSD", "BUSD", "FDUSD", "PYUSD", "USDP", "GUSD",
    "SUSD", "USDD", "USDJ", "CUSD", "FRAX", "LUSD", "CRVUSD", "GHO",
    "EURS", "EURT", "USDE", "SFRAX", "SUSDE", "MUSDE", "UST", "USDX",
    "HUSD", "OUSD", "ALUSD", "MIM", "DOLA", "BEAN", "USDK", "ZUSD",
    "RSV", "PAX", "USDN", "USDS", "EUSD", "USDH", "USDB",
})


def is_leveraged_token(symbol: str) -> bool:
    """Return True if the symbol is a leveraged/inverse token, not a real crypto."""
    return bool(_LEVERAGED_RE.search(symbol))


def is_leveraged_base(base: str) -> bool:
    """Return True if the *base* currency looks like a leveraged ETF token."""
    return bool(_ETF_BASE_RE.search(base))


def is_stablecoin(symbol: str) -> bool:
    """Return True if the symbol is a stablecoin pair (e.g. USDC_USDT)."""
    base = symbol.split("_")[0].upper()
    return base in _STABLECOIN_BASES


def is_excluded_asset(symbol: str) -> bool:
    """Return True if the symbol should be excluded from discovery pools.

    Checks for leveraged tokens AND stablecoins.
    """
    return is_leveraged_token(symbol) or is_stablecoin(symbol)


def filter_real_assets(symbols: list[str]) -> list[str]:
    """Remove leveraged tokens AND stablecoins from a list of symbols."""
    return [s for s in symbols if not is_excluded_asset(s)]
