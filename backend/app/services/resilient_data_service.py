"""Resilient data acquisition layer.

Provides a single `fetch_with_resilience` coroutine that wraps any async
data-fetch function with:

  1. Primary call
  2. Controlled retry (≤ 2 attempts, exponential back-off)
  3. Optional secondary / fallback call
  4. Short-TTL in-process cache
  5. UNKNOWN state when all sources fail  (never a hard FAIL)

Standardised log tags (grep-friendly):
  [DATA_PRIMARY_OK]
  [DATA_RETRY]
  [DATA_RETRY_OK]
  [DATA_PRIMARY_FAIL]
  [DATA_FALLBACK]
  [DATA_FALLBACK_EMPTY]
  [DATA_FALLBACK_FAIL]
  [DATA_CACHE_USED]
  [DATA_UNKNOWN]

Usage::

    from .resilient_data_service import fetch_with_resilience

    value, source = await fetch_with_resilience(
        key="depth:gate:BTC_USDT",
        primary_fn=lambda: fetch_gate_orderbook("BTC_USDT"),
        fallback_fn=lambda: fetch_binance_orderbook("BTC_USDT"),
        cache_ttl=30,
    )
    # source in {"primary", "retry", "fallback", "cache", "unknown"}
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable, Optional, Tuple

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, float, Any]] = {}

DEFAULT_TTL: float = 30.0
DEFAULT_RETRIES: int = 2
DEFAULT_RETRY_DELAY: float = 0.25


def _get_cache(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, ttl, value = entry
    if (time.monotonic() - stored_at) > ttl:
        _cache.pop(key, None)
        return None
    return value


def _set_cache(key: str, value: Any, ttl: float) -> None:
    _cache[key] = (time.monotonic(), ttl, value)
    if len(_cache) > 2_000:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)


def clear_cache(prefix: str = "") -> int:
    """Remove all cache entries whose key starts with *prefix*.

    Passing an empty string clears the entire cache.  Returns the number of
    entries removed.
    """
    if prefix:
        keys = [k for k in list(_cache) if k.startswith(prefix)]
    else:
        keys = list(_cache)
    for k in keys:
        _cache.pop(k, None)
    return len(keys)


async def fetch_with_resilience(
    key: str,
    primary_fn: Callable[[], Awaitable[Any]],
    *,
    fallback_fn: Optional[Callable[[], Awaitable[Any]]] = None,
    cache_ttl: float = DEFAULT_TTL,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> Tuple[Any, str]:
    """Fetch data with retry, fallback, and cache.

    Parameters
    ----------
    key:
        Cache / log identifier — use the format  ``<indicator>:<exchange>:<symbol>``,
        e.g. ``depth:gate:BTC_USDT``.
    primary_fn:
        Async callable that returns the data (or None / empty on soft failure).
    fallback_fn:
        Optional async callable used when primary + retries are all exhausted.
    cache_ttl:
        Seconds to keep a successful result in the in-process cache.
    retries:
        How many *extra* attempts after the first one (max total = 1 + retries).
    retry_delay:
        Base delay in seconds between retries (multiplied by attempt index).

    Returns
    -------
    (value, source)
        ``source`` is one of: ``"primary"`` | ``"retry"`` | ``"fallback"`` |
        ``"cache"`` | ``"unknown"``.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1 + retries):
        try:
            data = await primary_fn()
            if data is not None and data != [] and data != {}:
                _set_cache(key, data, cache_ttl)
                if attempt == 0:
                    logger.debug("[DATA_PRIMARY_OK] key=%s", key)
                    return data, "primary"
                logger.info("[DATA_RETRY_OK] key=%s attempt=%d", key, attempt)
                return data, "retry"
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                delay = retry_delay * (attempt + 1)
                logger.warning(
                    "[DATA_RETRY] key=%s attempt=%d/%d error=%s sleeping=%.2fs",
                    key, attempt + 1, retries, exc, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "[DATA_PRIMARY_FAIL] key=%s total_attempts=%d error=%s",
                    key, attempt + 1, exc,
                )

    if fallback_fn is not None:
        try:
            data = await fallback_fn()
            if data is not None and data != [] and data != {}:
                _set_cache(key, data, cache_ttl)
                logger.info("[DATA_FALLBACK] key=%s", key)
                return data, "fallback"
            logger.warning("[DATA_FALLBACK_EMPTY] key=%s", key)
        except Exception as exc:
            logger.warning("[DATA_FALLBACK_FAIL] key=%s error=%s", key, exc)

    cached = _get_cache(key)
    if cached is not None:
        logger.info("[DATA_CACHE_USED] key=%s", key)
        return cached, "cache"

    logger.warning("[DATA_UNKNOWN] key=%s last_error=%s", key, last_exc)
    return None, "unknown"
