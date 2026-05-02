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

The fix is a tiny distributed lock in Redis driven by a per-process
**supervisor** that keeps every replica eligible to take over:

  * ``SET gate_ws:leader <instance_id> NX EX 30`` — only one caller wins.
    The TTL bounds how long a crashed leader can hold the lock.
  * Periodic renewal every 10 s using a Lua check-and-set so we never
    extend a lock owned by a *different* instance.
  * Non-leader replicas run a continuous **candidate loop** that retries
    acquisition every ``CANDIDATE_POLL_INTERVAL_SECONDS``.  When the
    current leader dies, its lock expires within ~``LEADER_TTL_SECONDS``
    and the next survivor wins on its next poll.  Recovery target ≤ 35s.
  * On graceful shutdown the leader deletes the key (again with
    check-and-set) so the next replica can take over without waiting for
    the TTL.

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
# A non-leader replica retries acquisition on this cadence so that when
# the current leader dies (lock expires within ~LEADER_TTL_SECONDS) a
# survivor takes over within roughly TTL + this interval.
CANDIDATE_POLL_INTERVAL_SECONDS: int = 5

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
    """Background coroutine that renews the leader lock.

    Fires the ``on_leadership_lost`` callback (exactly once) the first
    time renewal returns ``False``.  Stops itself afterwards — the
    supervisor is responsible for tearing the WS down and restarting
    candidacy.
    """

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


class _GateWSSupervisor:
    """Continuous candidate-or-leader state machine for one process.

    Always-on responsibilities:
      * As candidate: every ``CANDIDATE_POLL_INTERVAL_SECONDS`` try
        ``SET NX EX``. (This is the failover loop — the previous
        single-shot ``_try_acquire_leader`` at startup left readers
        permanently idle if the leader later died.)
      * As leader: start the WS, register handlers, run the renewal
        loop, hold leadership until either (a) the renewal Lua script
        reports we are no longer owner, or (b) the supervisor is asked
        to stop.
      * On leadership loss: tear down the WS, release the lock if we
        still own it, and immediately loop back to candidate mode.
    """

    def __init__(self, redis, instance_id: str) -> None:
        self._redis = redis
        self._instance_id = instance_id
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        self._is_leader = False  # observable for tests + future /metrics gauge

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    @property
    def instance_id(self) -> str:
        return self._instance_id

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="gate_ws_supervisor")

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        if task is None:
            return
        try:
            # Give the supervisor a chance to drain its leader-mode
            # ``finally`` (release lock, stop WS) before we cancel.
            await asyncio.wait_for(task, timeout=15)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                acquired = await _try_acquire_leader(self._redis, self._instance_id)
                if acquired:
                    self._is_leader = True
                    logger.info(
                        "[gate-ws-leader] acquired gate_ws:leader (instance=%s)",
                        self._instance_id,
                    )
                    try:
                        await self._serve_as_leader()
                    finally:
                        self._is_leader = False
                else:
                    logger.debug(
                        "[gate-ws-leader] candidate: another instance holds the lock (instance=%s)",
                        self._instance_id,
                    )

                if self._stopping.is_set():
                    return
                # Wait before next acquisition attempt, but wake immediately
                # on shutdown.
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(),
                        timeout=CANDIDATE_POLL_INTERVAL_SECONDS,
                    )
                    return
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            logger.error("[gate-ws-leader] supervisor crashed: %s", exc, exc_info=True)
            raise

    async def _serve_as_leader(self) -> None:
        """Start the WS, drive renewal, and tear down on loss/stop."""
        # Lazy imports — keep this module decoupled from the WS client at import time.
        from ..websocket import gate_ws_client as _ws_client_mod

        spot_pairs = await _resolve_spot_pairs()
        if not spot_pairs:
            logger.warning(
                "[gate-ws-leader] no spot pairs resolved — releasing lock and going back to candidate mode"
            )
            await _release_leader(self._redis, self._instance_id)
            return

        api_key, api_secret = await _load_gate_credentials()

        try:
            client = await _ws_client_mod.start_gate_ws(
                api_key=api_key,
                api_secret=api_secret,
                contracts=[],   # futures.trades is out of scope for #171
                spot_pairs=spot_pairs,
                instance_id=self._instance_id,
            )
            from ..websocket.event_handlers import register_all_handlers
            register_all_handlers(client)
            logger.info(
                "[gate-ws-leader] Gate WS started (instance=%s, spot_pairs=%d)",
                self._instance_id, len(spot_pairs),
            )
        except Exception as exc:
            logger.error(
                "[gate-ws-leader] failed to start Gate WS: %s — releasing lock",
                exc, exc_info=True,
            )
            await _release_leader(self._redis, self._instance_id)
            return

        leadership_lost = asyncio.Event()

        async def _on_lost() -> None:
            leadership_lost.set()

        runner = _LeaderRunner(self._redis, self._instance_id, _on_lost)
        runner.start()

        stop_task = asyncio.create_task(self._stopping.wait())
        lost_task = asyncio.create_task(leadership_lost.wait())
        try:
            done, pending = await asyncio.wait(
                {stop_task, lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
                try:
                    await p
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            await runner.stop()
            try:
                await _ws_client_mod.stop_gate_ws()
            except Exception as exc:
                logger.warning(
                    "[gate-ws-leader] stop_gate_ws() failed: %s", exc
                )
            # Release lock only if we still own it (Lua check-and-set).
            await _release_leader(self._redis, self._instance_id)


async def start_gate_ws_with_leader_election() -> Optional[Callable[[], Awaitable[None]]]:
    """Start the supervisor and return a shutdown coroutine.

    Returns ``None`` when:
      * the feature flag ``ENABLE_GATE_WS`` is not ``"1"``, or
      * Redis is unavailable.

    When enabled, the returned shutdown coroutine MUST be awaited from
    the FastAPI lifespan ``finally``.  The supervisor keeps trying to
    acquire leadership for the lifetime of the process; this replica may
    start out as a reader and become the leader later — when the current
    leader dies, its 30 s lock expires and the next ``_try_acquire_leader``
    call wins within ~``CANDIDATE_POLL_INTERVAL_SECONDS``.
    """
    if os.environ.get("ENABLE_GATE_WS") != "1":
        logger.info("[gate-ws-leader] ENABLE_GATE_WS != 1 — Gate WS disabled (REST polling only)")
        return None

    redis = await get_async_redis()
    if redis is None:
        logger.warning("[gate-ws-leader] Redis unavailable — cannot run leader election; WS disabled")
        return None

    instance_id = _resolve_instance_id()
    supervisor = _GateWSSupervisor(redis, instance_id)
    supervisor.start()
    logger.info(
        "[gate-ws-leader] supervisor started (instance=%s, candidate_poll=%ds, leader_ttl=%ds)",
        instance_id, CANDIDATE_POLL_INTERVAL_SECONDS, LEADER_TTL_SECONDS,
    )

    async def _shutdown() -> None:
        logger.info("[gate-ws-leader] lifespan shutdown — stopping supervisor")
        await supervisor.stop()

    return _shutdown


__all__ = [
    "LEADER_KEY",
    "LEADER_TTL_SECONDS",
    "LEADER_RENEW_INTERVAL_SECONDS",
    "CANDIDATE_POLL_INTERVAL_SECONDS",
    "start_gate_ws_with_leader_election",
    # Exported for tests:
    "_GateWSSupervisor",
    "_try_acquire_leader",
    "_renew_leader",
    "_release_leader",
    "_resolve_instance_id",
]
