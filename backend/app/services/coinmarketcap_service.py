"""CoinMarketCap service for fetching market caps by symbol."""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

import httpx

from ..utils.symbol_filters import is_leveraged_base

logger = logging.getLogger(__name__)

CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"
CMC_BATCH_SIZE = 100
_PAIR_SUFFIX_RE = re.compile(r"(?:_USDT|USDT)$")


def normalize_market_cap_symbols(symbols: Iterable[str]) -> list[str]:
    """Normalize symbols to CMC base tickers, removing duplicates and leveraged tokens."""
    normalized: list[str] = []
    seen: set[str] = set()

    for symbol in symbols:
        base = _PAIR_SUFFIX_RE.sub("", (symbol or "").upper().strip())
        if not base or is_leveraged_base(base) or base in seen:
            continue
        normalized.append(base)
        seen.add(base)

    return normalized


def extract_market_caps(payload: dict[str, Any]) -> dict[str, float]:
    """Extract a symbol → market cap map from CoinMarketCap quotes payload."""
    result: dict[str, float] = {}

    for symbol, raw_entries in (payload.get("data") or {}).items():
        entries = raw_entries if isinstance(raw_entries, list) else [raw_entries]
        for entry in entries:
            market_cap = ((entry or {}).get("quote") or {}).get("USD", {}).get("market_cap")
            try:
                market_cap_value = float(market_cap)
            except (TypeError, ValueError):
                continue
            if market_cap_value > 0:
                result[symbol.upper()] = market_cap_value
                break

    return result


async def fetch_market_caps(symbols: list[str], api_key: str) -> dict[str, float]:
    """Fetch market caps from CoinMarketCap for the requested symbols."""
    normalized_symbols = normalize_market_cap_symbols(symbols)
    if not normalized_symbols:
        return {}

    headers = {
        "X-CMC_PRO_API_KEY": api_key,
        "Accept": "application/json",
    }
    result: dict[str, float] = {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for start in range(0, len(normalized_symbols), CMC_BATCH_SIZE):
            batch = normalized_symbols[start:start + CMC_BATCH_SIZE]
            try:
                response = await client.get(
                    CMC_QUOTES_URL,
                    headers=headers,
                    params={
                        "symbol": ",".join(batch),
                        "convert": "USD",
                        "skip_invalid": "true",
                    },
                )
                response.raise_for_status()
                result.update(extract_market_caps(response.json()))
            except Exception as exc:
                logger.warning(
                    "CoinMarketCap batch failed for %d symbols (sample: %s): %s",
                    len(batch),
                    batch[:5],
                    exc,
                )

    logger.info(
        "CoinMarketCap: fetched market caps for %d/%d requested symbols.",
        len(result),
        len(normalized_symbols),
    )
    return result
