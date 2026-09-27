"""Async Redis singleton — one shared ``redis.asyncio.Redis`` per event loop.

Why a loop-local singleton
---------------

Every hot path that needs Redis (the Gate WS trade-buffer handler, the
buffer-first read in ``order_flow_service``, the Gate-WS leader lock in
``main.py::lifespan``) must share **one** connection pool within their event loop.  Calling
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
  and cached on the owning event loop. Celery loops never share transports.  Tests can call
  ``reset_async_redis()`` between cases to drop the cached client.

Usage::

    from app.services.redis_client import get_async_redis

    rc = await get_async_redis()
    if rc is None:
        return  # Redis unreachable — caller decides what to do
    await rc.zadd("trades_buffer:BTC_USDT", {payload: score})
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Keep the state on the loop itself: a process-wide client reuses sockets from
# an already closed Celery loop. A module-level loop map would retain loops.
@dataclass
class _LoopState:
    pid: int
    client: object = None
    retry_after: float = 0.0


def _state() -> _LoopState:
    loop = asyncio.get_running_loop()
    state = getattr(loop, "_scalpyn_redis_state", None)
    if state is None or state.pid != os.getpid():
        state = _LoopState(pid=os.getpid())
        setattr(loop, "_scalpyn_redis_state", state)
    return state


# Time to wait between init attempts after a failure.
INIT_RETRY_COOLDOWN_SECONDS: float = 30.0


async def get_async_redis():
    """Return the shared async Redis client, or ``None`` if it cannot be created.

    On init failure the next ``INIT_RETRY_COOLDOWN_SECONDS`` worth of
    calls return ``None`` immediately, then the next call retries.  This
    lets a transient Redis startup race resolve itself without forcing a
    process restart, while still protecting hot loops from retry storms.
    Call :func:`reset_async_redis` to clear the cooldown and the cached
    client (used by tests and by the lifespan shutdown).
    """
    state = _state()

    if state.client is not None:
        return state.client
    now = time.monotonic()
    if now < state.retry_after:
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
        state.client = client
        state.retry_after = 0.0
        logger.info("[redis] async client initialised (url=%s)", _redacted_url(settings.REDIS_URL))
        return state.client
    except Exception as exc:
        state.retry_after = now + INIT_RETRY_COOLDOWN_SECONDS
        logger.warning(
            "[redis] async client init failed: %s — feature degraded, retrying in %.0fs",
            exc, INIT_RETRY_COOLDOWN_SECONDS,
        )
        return None


async def reset_async_redis() -> None:
    """Drop the current loop's cached client; the next ``get_async_redis()`` reconnects.

    Used by tests and by the lifespan shutdown so the next process boot
    or test case starts from a clean slate.
    """
    state = _state()
    client = state.client
    state.client = None
    state.retry_after = 0.0
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
    state = _state()
    state.client = client
    state.retry_after = 0.0


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
