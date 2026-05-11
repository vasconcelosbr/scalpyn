"""Cross-process pub/sub bus for decision audit events.

Decisions are recorded by ``decision_audit_service._record_decision_raw``
on three different processes — the FastAPI server (executes
``execute_buy``? No: only the workers do) and the Celery worker pool
(``execute_buy``, ``futures_scanner`` task) — and consumed by the SSE
endpoint at ``/api/live/log-stream`` that runs only on the API. To
deliver worker-side decisions to API-side SSE clients we MUST hop
through a shared broker.

Implementation
--------------
* **Redis Pub/Sub** is the canonical fan-out path. Channel name is
  ``scalpyn:decision_events``. Publishers fire-and-forget JSON-encoded
  payloads. Subscribers (the SSE endpoint) maintain their own
  long-lived ``redis.asyncio`` client + pubsub object with auto
  reconnect (mirrors the pattern in ``realtime_bridge.py``).
* **In-process fan-out** is *also* kept for the rare case where a
  publisher and a subscriber live in the same process (single-process
  dev mode, unit tests). It is not relied upon for cross-process
  delivery — the Redis path is. Per-subscriber bounded queue
  (``maxsize=_MAX_BUFFER``); on overflow the *oldest* event is dropped
  so a slow client cannot starve fast ones nor leak memory.
* **Tenancy is the consumer's job.** The published payload carries
  ``user_id``; the SSE endpoint filters per-caller and drops events
  where ``user_id`` is missing.

If Redis is unavailable, ``publish_decision_event_async`` silently
falls back to the local fan-out only (no exception ever propagates).
This preserves the audit path's robustness — observability degrades
gracefully when the broker is down.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional, Set

logger = logging.getLogger(__name__)

# Redis channel for cross-process decision events.
CHANNEL = "scalpyn:decision_events"

_MAX_BUFFER = 500

# Local in-process subscribers. ``Set`` mutation is safe under the
# single-threaded asyncio loop; we still snapshot via ``list()`` at
# publish time so a concurrent subscribe/unsubscribe cannot mutate the
# iteration.
_subscribers: Set[asyncio.Queue] = set()


# ── Local (in-process) fan-out ────────────────────────────────────────


def subscribe() -> asyncio.Queue:
    """Register a new in-process subscriber queue and return it.

    Caller MUST :func:`unsubscribe` when done (e.g. in a ``finally``
    block) — otherwise the queue stays referenced and slowly leaks
    memory as events keep being enqueued.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_BUFFER)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    """Remove a subscriber queue (idempotent)."""
    _subscribers.discard(queue)


def _fanout_local(event: Dict[str, Any]) -> None:
    """Push ``event`` into every local subscriber queue (drop-oldest on full)."""
    for queue in list(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except Exception:
                pass
        except Exception:  # noqa: BLE001 — defensive: never propagate
            logger.debug(
                "decision_event_bus: local subscriber put failed", exc_info=True
            )


def subscriber_count() -> int:
    """Return the current number of local in-process subscribers (diagnostics)."""
    return len(_subscribers)


# ── Cross-process publish (Redis) ─────────────────────────────────────


async def publish_decision_event_async(event: Dict[str, Any]) -> None:
    """Publish ``event`` to Redis (cross-process) AND local subscribers.

    Never raises. Redis failures degrade silently — the audit row is
    already persisted by the caller, this is observability only. Local
    fan-out always runs so single-process usage and unit tests keep
    working even without a broker.
    """
    # Always fan out locally first — this is sync, cannot fail in any
    # interesting way, and serves the same-process subscriber case.
    _fanout_local(event)

    try:
        from ..services.redis_client import get_async_redis
        client = await get_async_redis()
        if client is None:
            return
        payload = json.dumps(event, default=str, ensure_ascii=False)
        await client.publish(CHANNEL, payload)
    except Exception:
        # Don't even log at warning level — Redis pub/sub being down
        # is something the operator already monitors via the existing
        # /api/system/* surface, and a noisy log per audit row would
        # drown out everything else during a Redis outage.
        logger.debug("decision_event_bus: redis publish failed", exc_info=True)


# ── Cross-process subscribe (Redis) ───────────────────────────────────


async def redis_event_stream(
    reconnect_delay_base: float = 1.0,
    reconnect_delay_max: float = 30.0,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Async generator yielding decoded events from the Redis channel.

    Maintains its own dedicated ``redis.asyncio`` client (matching the
    pattern in ``realtime_bridge.py``) with auto-reconnect + exponential
    back-off so the SSE endpoint survives transient broker drops without
    closing the client connection. Yields one ``dict`` per message;
    malformed payloads are skipped.

    The generator only exits on cancellation (``GeneratorExit`` /
    ``CancelledError``) — the caller is expected to enforce its own
    timeouts (e.g. SSE heartbeat).
    """
    from ..config import settings

    delay = reconnect_delay_base
    while True:
        pubsub = None
        client = None
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]

            client = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_keepalive=True,
                health_check_interval=30,
            )
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(CHANNEL)
            delay = reconnect_delay_base  # reset back-off on successful connect

            async for raw in pubsub.listen():
                if raw is None:
                    continue
                data = raw.get("data")
                if not isinstance(data, str):
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    logger.warning(
                        "decision_event_bus: malformed JSON dropped: %.200s",
                        data,
                    )
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "decision_event_bus: subscriber loop error (reconnecting in %.1fs): %s",
                delay, exc,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, reconnect_delay_max)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(CHANNEL)
                    await pubsub.close()
                except Exception:
                    pass
            if client is not None:
                try:
                    closer = getattr(client, "aclose", None) or getattr(client, "close")
                    await closer()
                except Exception:
                    pass


# ── Backwards-compatible sync alias (DEPRECATED) ──────────────────────


def publish_decision_event(event: Dict[str, Any]) -> None:
    """Deprecated sync alias — local fan-out only (no Redis hop).

    Retained so any synchronous publisher path continues to work; new
    code must use :func:`publish_decision_event_async` so cross-process
    SSE delivery actually happens.
    """
    _fanout_local(event)
