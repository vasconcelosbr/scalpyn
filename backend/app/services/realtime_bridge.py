"""Redis pub/sub bridge for cross-process WebSocket broadcasting.

Celery workers and the uvicorn server run in separate OS processes. The
``ConnectionManager`` singleton in ``api/websocket.py`` holds actual WebSocket
connections only in the uvicorn process. Any attempt to call
``broadcast_decision_created()`` from inside a Celery task is a no-op because
that process has no connected clients.

This module provides two complementary halves of an IPC bridge:

* **Publisher** (Celery-side): ``publish_decision_event()`` serialises the
  decision payload and publishes it to the Redis channel
  ``scalpyn:decisions:events``. It uses a plain synchronous Redis client so
  that it works inside both sync Celery tasks and async pipeline-scan loops
  (the async loop in pipeline_scan already wraps Celery's sync worker thread).

* **Subscriber** (uvicorn-side): ``start_decision_event_subscriber()`` starts
  an ``asyncio`` background task that subscribes to the same channel using the
  async ``redis.asyncio`` client. Every message received is forwarded to
  ``ConnectionManager.broadcast()``, delivering it to all connected browsers.

Failure modes are handled gracefully:

* Redis unavailable at publish time → warning logged, broadcast silently
  dropped (pipeline scan continues normally).
* Redis unavailable at subscribe time → subscriber retries with exponential
  back-off (max 30 s) and logs a structured error each retry cycle.
* Subscriber task crashes → the ``_subscriber_loop`` catch-all restarts the
  connection from scratch to avoid a permanently silent dead subscriber.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_CHANNEL = "scalpyn:decisions:events"
_RECONNECT_DELAY_BASE = 1.0   # seconds
_RECONNECT_DELAY_MAX = 30.0   # seconds


# ---------------------------------------------------------------------------
# Publisher (synchronous — safe to call from Celery task or async loop)
# ---------------------------------------------------------------------------

def publish_decision_event(payload: dict[str, Any]) -> None:
    """Publish a decision event to Redis so all uvicorn replicas forward it.

    Called from ``pipeline_scan._run_pipeline_scan()`` (async, inside Celery
    worker) instead of the old ``broadcast_decision_created()`` which only
    reached in-process WebSocket connections.

    Failures are logged but never re-raised — a missed real-time push must
    never abort the pipeline scan.
    """
    try:
        import redis as redis_lib  # sync redis
        from ..config import settings

        client = redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        message = json.dumps({
            "type": "decision.created",
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "pipeline_scan",
        })
        client.publish(_CHANNEL, message)
        logger.debug(
            "[RealtimeBridge] decision_event_published | symbol=%s | event=%s",
            payload.get("symbol"), payload.get("event_type"),
        )
    except Exception as exc:
        logger.warning(
            "[RealtimeBridge] decision_event_published FAILED (non-fatal): %s", exc,
        )


# ---------------------------------------------------------------------------
# Subscriber (async — runs as an asyncio background task in uvicorn)
# ---------------------------------------------------------------------------

async def _subscriber_loop() -> None:
    """Continuously subscribe to the Redis channel and forward to WS manager.

    Runs forever inside the uvicorn process. Reconnects automatically with
    exponential back-off whenever the Redis connection drops.
    """
    from ..api.websocket import manager as ws_manager
    from ..config import settings

    delay = _RECONNECT_DELAY_BASE

    while True:
        pubsub = None
        try:
            import redis.asyncio as aioredis  # async redis

            client = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(_CHANNEL)
            logger.info("[RealtimeBridge] Subscribed to channel: %s", _CHANNEL)
            delay = _RECONNECT_DELAY_BASE  # reset back-off on successful connect

            async for raw in pubsub.listen():
                if raw is None:
                    continue
                data = raw.get("data")
                if not isinstance(data, str):
                    continue
                try:
                    message = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("[RealtimeBridge] Received malformed JSON: %.200s", data)
                    continue

                msg_type = message.get("type")
                msg_payload = message.get("payload", {})

                logger.debug(
                    "[RealtimeBridge] decision_event_received | type=%s | symbol=%s",
                    msg_type, msg_payload.get("symbol"),
                )

                await ws_manager.broadcast("decisions", {
                    "type": msg_type,
                    "data": msg_payload,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })

        except asyncio.CancelledError:
            logger.info("[RealtimeBridge] Subscriber task cancelled — shutting down")
            break
        except Exception as exc:
            logger.error(
                "[RealtimeBridge] redis_timeout | subscriber error (reconnecting in %.0fs): %s",
                delay, exc,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_DELAY_MAX)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.close()
                except Exception:
                    pass


async def start_decision_event_subscriber() -> asyncio.Task:
    """Start the Redis→WebSocket bridge as a background asyncio task.

    Called once from ``main.py`` during the FastAPI lifespan startup. The
    returned task is cancelled gracefully on shutdown.
    """
    task = asyncio.create_task(_subscriber_loop(), name="decision_event_subscriber")
    logger.info("[RealtimeBridge] Decision event subscriber started.")
    return task
