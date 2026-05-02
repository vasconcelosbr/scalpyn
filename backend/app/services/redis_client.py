"""Async Redis singleton — one shared ``redis.asyncio.Redis`` per process.

Why a singleton
---------------

Every hot path that needs Redis (the Gate WS trade-buffer handler, the
buffer-first read in ``order_flow_service``, the Gate-WS leader lock in
``main.py::lifespan``) must share **one** connection pool.  Calling
``redis.asyncio.from_url(...)`` per message would (a) leak file
descriptors under load, (b) double connection setup latency on every WS
trade frame, and (c) make pool stats meaningless because each call would
have its own pool of size 1.

Contract
--------

* ``decode_responses=False`` — we store raw JSON bytes in the trade
  buffer and prefer parsing on read; mixing decode modes across callers
  would force every caller to know the buffer's encoding.
* Short ``socket_connect_timeout`` (3 s) so a Redis outage surfaces as a
  log warning + ``None`` return, not as a hung WS dispatcher.
* The client is created lazily on the first ``get_async_redis()`` call
  and cached in a module-level variable.  Tests can call
  ``reset_async_redis()`` between cases to drop the cached client.

Usage::

    from app.services.redis_client import get_async_redis

    rc = await get_async_redis()
    if rc is None:
        return  # Redis unreachable — caller decides what to do
    await rc.zadd("trades_buffer:BTC_USDT", {payload: score})
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


_async_client = None  # type: ignore[var-annotated]
_init_failed = False


async def get_async_redis():
    """Return the shared async Redis client, or ``None`` if it cannot be created.

    The first failure is cached so callers in hot loops do not retry on
    every message; call :func:`reset_async_redis` to clear the failure
    and force a reconnect attempt.
    """
    global _async_client, _init_failed

    if _async_client is not None:
        return _async_client
    if _init_failed:
        return None

    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        from ..config import settings

        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=False,
            socket_connect_timeout=3,
            socket_keepalive=True,
            health_check_interval=30,
        )
        _async_client = client
        logger.info("[redis] async client initialised (url=%s)", _redacted_url(settings.REDIS_URL))
        return _async_client
    except Exception as exc:
        _init_failed = True
        logger.warning("[redis] async client init failed: %s — feature degraded", exc)
        return None


async def reset_async_redis() -> None:
    """Drop the cached client; the next ``get_async_redis()`` reconnects.

    Used by tests and by the lifespan shutdown so the next process boot
    or test case starts from a clean slate.
    """
    global _async_client, _init_failed
    client = _async_client
    _async_client = None
    _init_failed = False
    if client is not None:
        try:
            # ``aclose`` is the new name in redis-py ≥5.0.1; ``close``
            # falls back for older releases.
            closer = getattr(client, "aclose", None) or getattr(client, "close")
            await closer()
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("[redis] close() failed during reset: %s", exc)


def set_async_redis(client) -> None:
    """Inject a client (used by tests with ``fakeredis.aioredis``)."""
    global _async_client, _init_failed
    _async_client = client
    _init_failed = False


def _redacted_url(url: str) -> str:
    """Strip the password from a redis:// URL for safe logging."""
    if "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if "://" in head:
        scheme, rest = head.split("://", 1)
        if ":" in rest:
            user, _ = rest.split(":", 1)
            return f"{scheme}://{user}:***@{tail}"
    return f"***@{tail}"


__all__ = [
    "get_async_redis",
    "reset_async_redis",
    "set_async_redis",
]
