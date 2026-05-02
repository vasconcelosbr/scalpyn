"""Leader-election + lifespan glue for the Gate.io WebSocket client (Task #171).

Why
---

In production (Cloud Run) the FastAPI service may run with N>1 replicas
behind the load balancer.  If every replica opened its own
``GateWSClient`` we would (a) multiply Gate's WS connection quota by N,
(b) write the same trade N times into Redis (ZADD on the same member
deduplicates the score but every replica still pays the network round
trip), and (c) double the Prometheus counters because each replica
increments ``gate_trades_received_total``.

The fix is a tiny distributed lock in Redis:

  * ``SET gate_ws:leader <instance_id> NX EX 30`` — only the first
    caller wins.  ``EX 30`` is the lock TTL: if the leader hangs or
    crashes the lock auto-expires and another replica takes over within
    ~30 s.
  * Periodic renewal every 10 s using a Lua check-and-set script so we
    never extend a lock owned by a *different* instance (a naive
    ``EXPIRE`` after a temporary stall could otherwise stretch a peer's
    lock and cause a double-leader window).
  * On graceful shutdown the leader deletes the key (again with
    check-and-set to be safe) so the next replica can take over without
    waiting for the TTL.

This module is intentionally framework-agnostic — ``main.py`` calls
``start_gate_ws_with_leader_election()`` from inside the FastAPI
lifespan and awaits the returned coroutine on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import Awaitable, Callable, Optional

from sqlalchemy import select, text

from ..database import AsyncSessionLocal
from ..models.exchange_connection import ExchangeConnection
from .redis_client import get_async_redis

logger = logging.getLogger(__name__)


LEADER_KEY: bytes = b"gate_ws:leader"
LEADER_TTL_SECONDS: int = 30
LEADER_RENEW_INTERVAL_SECONDS: int = 10

# Lua script: extend the TTL only if we are still the owner.  Returning
# 1 means "we are still the leader and the lock has been renewed";
# returning 0 means "someone else owns the lock now".
_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
else
    return 0
end
"""

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


def _resolve_instance_id() -> str:
    """Stable identifier for this process across leader-election operations.

    Cloud Run sets ``K_SERVICE``/``K_REVISION`` but not a per-instance
    string; ``HOSTNAME`` is the container hostname.  Combining the
    hostname with a UUID guarantees uniqueness across restarts and
    across replicas that happen to share the same hostname.
    """
    host = os.environ.get("HOSTNAME") or socket.gethostname() or "unknown"
    return f"{host}-{uuid.uuid4().hex[:8]}"


async def _try_acquire_leader(redis, instance_id: str) -> bool:
    """Attempt to atomically claim the leader lock for ``instance_id``."""
    try:
        # ``set(..., nx=True, ex=...)`` returns True iff the key was set.
        ok = await redis.set(
            LEADER_KEY,
            instance_id.encode("utf-8"),
            ex=LEADER_TTL_SECONDS,
            nx=True,
        )
        return bool(ok)
    except Exception as exc:
        logger.warning("[gate-ws-leader] acquire failed: %s", exc)
        return False


async def _renew_leader(redis, instance_id: str) -> bool:
    """Extend the leader TTL only if we still own the lock."""
    try:
        result = await redis.eval(
            _RENEW_SCRIPT,
            1,
            LEADER_KEY,
            instance_id.encode("utf-8"),
            str(LEADER_TTL_SECONDS).encode("utf-8"),
        )
        return bool(result)
    except Exception as exc:
        logger.warning("[gate-ws-leader] renew eval failed: %s", exc)
        return False


async def _release_leader(redis, instance_id: str) -> None:
    """Release the lock if (and only if) we still own it."""
    try:
        await redis.eval(
            _RELEASE_SCRIPT,
            1,
            LEADER_KEY,
            instance_id.encode("utf-8"),
        )
    except Exception as exc:
        logger.warning("[gate-ws-leader] release eval failed: %s", exc)


async def _load_gate_credentials() -> tuple[str, str]:
    """Best-effort load of the first active Gate API credentials.

    Mirrors the pattern used by ``tasks/macro_regime_update.py``.  When
    no active connection exists (or decryption fails) we return empty
    strings: the WS auth handshake will be rejected by Gate, but the
    *public* ``spot.trades`` channel still streams normally — which is
    all this task needs.
    """
    try:
        from ..utils.encryption import decrypt
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[gate-ws-leader] encryption helper unavailable: %s", exc)
        return "", ""

    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(ExchangeConnection)
                .where(ExchangeConnection.is_active == True)  # noqa: E712
                .order_by(ExchangeConnection.execution_priority)
                .limit(1)
            )
            conn = res.scalars().first()
        if conn is None:
            logger.info("[gate-ws-leader] no active ExchangeConnection — using empty creds (public channels only)")
            return "", ""
        api_key = decrypt(conn.api_key_encrypted).strip()
        api_secret = decrypt(conn.api_secret_encrypted).strip()
        return api_key, api_secret
    except Exception as exc:
        logger.warning("[gate-ws-leader] failed to load credentials: %s — using empty creds", exc)
        return "", ""


async def _resolve_spot_pairs() -> list[str]:
    """Reuse the same symbol universe as ``microstructure_scheduler_service``.

    Union of ``pipeline_watchlist_assets.symbol`` and
    ``market_metadata.symbol``, capped at 500 — the same query lives in
    ``services/microstructure_scheduler_service._collect_symbols``.
    """
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(text("""
                SELECT DISTINCT symbol
                FROM pipeline_watchlist_assets
                WHERE symbol IS NOT NULL AND symbol <> ''
                UNION
                SELECT DISTINCT symbol
                FROM market_metadata
                WHERE symbol IS NOT NULL AND symbol <> ''
                LIMIT 500
            """))).fetchall()
        return [r.symbol for r in rows if r.symbol]
    except Exception as exc:
        logger.warning("[gate-ws-leader] failed to resolve spot_pairs: %s", exc)
        return []


class _LeaderRunner:
    """Background coroutine that renews the leader lock and stops the WS on loss."""

    def __init__(
        self,
        redis,
        instance_id: str,
        on_leadership_lost: Callable[[], Awaitable[None]],
    ) -> None:
        self._redis = redis
        self._instance_id = instance_id
        self._on_lost = on_leadership_lost
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="gate_ws_leader_renew")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=LEADER_RENEW_INTERVAL_SECONDS,
                )
                break  # stopping
            except asyncio.TimeoutError:
                pass

            still_leader = await _renew_leader(self._redis, self._instance_id)
            if still_leader:
                continue

            logger.warning(
                "[gate-ws-leader] lost leadership (lock missing or owned by another instance) — stopping local WS"
            )
            try:
                await self._on_lost()
            except Exception as exc:
                logger.warning("[gate-ws-leader] on_lost callback failed: %s", exc)
            return


async def start_gate_ws_with_leader_election() -> Optional[Callable[[], Awaitable[None]]]:
    """Try to start the Gate WS as the leader; return a shutdown coroutine.

    Returns ``None`` when:
      * the feature flag ``ENABLE_GATE_WS`` is not ``"1"``, or
      * Redis is unavailable, or
      * another replica already holds the lock.

    When non-None, the returned async callable should be awaited from
    the FastAPI lifespan ``finally`` to release the lock and stop the WS.
    """
    if os.environ.get("ENABLE_GATE_WS") != "1":
        logger.info("[gate-ws-leader] ENABLE_GATE_WS != 1 — Gate WS disabled (REST polling only)")
        return None

    redis = await get_async_redis()
    if redis is None:
        logger.warning("[gate-ws-leader] Redis unavailable — cannot run leader election; WS disabled")
        return None

    instance_id = _resolve_instance_id()
    acquired = await _try_acquire_leader(redis, instance_id)
    if not acquired:
        logger.info(
            "[gate-ws-leader] another instance holds gate_ws:leader — staying as reader (instance=%s)",
            instance_id,
        )
        return None

    logger.info("[gate-ws-leader] acquired gate_ws:leader (instance=%s)", instance_id)

    # Lazy imports — keep this module decoupled from the WS client at import time.
    from ..websocket.gate_ws_client import start_gate_ws, stop_gate_ws
    from ..websocket.event_handlers import register_all_handlers

    spot_pairs = await _resolve_spot_pairs()
    if not spot_pairs:
        logger.warning("[gate-ws-leader] no spot pairs resolved — releasing lock and skipping WS")
        await _release_leader(redis, instance_id)
        return None

    api_key, api_secret = await _load_gate_credentials()

    try:
        client = await start_gate_ws(
            api_key=api_key,
            api_secret=api_secret,
            contracts=[],   # futures.trades is out of scope for #171
            spot_pairs=spot_pairs,
            instance_id=instance_id,
        )
        register_all_handlers(client)
        logger.info(
            "[gate-ws-leader] Gate WS started (instance=%s, spot_pairs=%d)",
            instance_id, len(spot_pairs),
        )
    except Exception as exc:
        logger.error("[gate-ws-leader] failed to start Gate WS: %s — releasing lock", exc, exc_info=True)
        await _release_leader(redis, instance_id)
        return None

    # ── Renewal loop ────────────────────────────────────────────────────────
    async def _on_lost() -> None:
        try:
            await stop_gate_ws()
        except Exception as exc:
            logger.warning("[gate-ws-leader] stop_gate_ws() failed: %s", exc)

    runner = _LeaderRunner(redis, instance_id, _on_lost)
    runner.start()

    async def _shutdown() -> None:
        logger.info("[gate-ws-leader] lifespan shutdown — stopping renewal + WS + releasing lock")
        await runner.stop()
        try:
            await stop_gate_ws()
        except Exception as exc:
            logger.warning("[gate-ws-leader] stop_gate_ws() during shutdown failed: %s", exc)
        await _release_leader(redis, instance_id)

    return _shutdown


__all__ = [
    "LEADER_KEY",
    "LEADER_TTL_SECONDS",
    "LEADER_RENEW_INTERVAL_SECONDS",
    "start_gate_ws_with_leader_election",
    # Exported for tests:
    "_try_acquire_leader",
    "_renew_leader",
    "_release_leader",
    "_resolve_instance_id",
]
