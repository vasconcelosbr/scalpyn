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
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from sqlalchemy import select, text

from ..database import AsyncSessionLocal
from ..models.exchange_connection import ExchangeConnection
from .redis_client import get_async_redis

logger = logging.getLogger(__name__)


LEADER_KEY: bytes = b"gate_ws:leader"
LEADER_TTL_SECONDS: int = 30
LEADER_RENEW_INTERVAL_SECONDS: int = 10
# Cross-instance trigger published by ``refresh_subscriptions()`` and
# polled by the leader's renew loop on every tick. The value is a
# millisecond Unix timestamp; if it exceeds the leader's ``serve_started_ms``
# the leader gracefully drops its WS connection and lets the supervisor
# re-acquire + re-resolve the symbol universe (Task #194 — etapa 3).
REFRESH_REQUEST_KEY: bytes = b"gate_ws:refresh_request"
REFRESH_REQUEST_TTL_SECONDS: int = 300
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
    """Best-effort load of Gate API credentials.

    The task spec (line 47) calls for env-based credentials
    (``GATE_API_KEY`` / ``GATE_API_SECRET``).  We honor that first and
    fall back to the first active ``ExchangeConnection`` row only when
    the env vars are unset — useful in dev where credentials are
    typically configured via the UI.

    When neither source yields credentials we return empty strings: the
    WS auth handshake is then *skipped* by ``GateWSClient._send_auth``
    (see that method's docstring) and the public ``spot.trades`` channel
    streams normally — which is all this task needs.
    """
    env_key = (os.environ.get("GATE_API_KEY") or "").strip()
    env_secret = (os.environ.get("GATE_API_SECRET") or "").strip()
    if env_key and env_secret:
        logger.info("[gate-ws-leader] using Gate credentials from env (GATE_API_KEY)")
        return env_key, env_secret

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
            logger.info(
                "[gate-ws-leader] no GATE_API_KEY env var and no active ExchangeConnection — "
                "using empty creds (public channels only)"
            )
            return "", ""
        api_key = decrypt(conn.api_key_encrypted).strip()
        api_secret = decrypt(conn.api_secret_encrypted).strip()
        logger.info("[gate-ws-leader] using Gate credentials from ExchangeConnection (env vars unset)")
        return api_key, api_secret
    except Exception as exc:
        logger.warning("[gate-ws-leader] failed to load credentials: %s — using empty creds", exc)
        return "", ""


async def _resolve_spot_symbols() -> list[str]:
    """Resolve the SPOT universe from approved pool coins.

    Queries ``pool_coins`` joined with ``pools`` where ``market_type = 'spot'``
    and the coin is active.  Falls back to the legacy union query
    (``pipeline_watchlist_assets`` ∪ ``market_metadata``) when no spot
    pool coins exist so that deployments without explicit pool setup keep
    working.
    """
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(text("""
                SELECT DISTINCT pc.symbol
                FROM pool_coins pc
                JOIN pools p ON p.id = pc.pool_id
                WHERE pc.is_active = TRUE
                  AND pc.is_approved = TRUE
                  AND p.market_type = 'spot'
                  AND pc.symbol IS NOT NULL
                  AND pc.symbol <> ''
                LIMIT 500
            """))).fetchall()
        symbols = [r.symbol for r in rows if r.symbol]
        if symbols:
            return symbols
        # Legacy fallback: no pool_coins with market_type='spot' — fall back
        # to the pipeline_watchlist_assets ∪ market_metadata universe so
        # systems without explicit pool configuration keep working.
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
        logger.warning("[gate-ws-leader] failed to resolve spot symbols: %s", exc)
        return []


async def _resolve_futures_symbols() -> list[str]:
    """Resolve the FUTURES universe from approved pool coins.

    Queries ``pool_coins`` joined with ``pools`` where
    ``market_type = 'futures'`` and the coin is active.  Returns an
    empty list when no futures pool coins exist — the WS will not
    subscribe to any futures contracts in that case.
    """
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(text("""
                SELECT DISTINCT pc.symbol
                FROM pool_coins pc
                JOIN pools p ON p.id = pc.pool_id
                WHERE pc.is_active = TRUE
                  AND p.market_type = 'futures'
                  AND pc.symbol IS NOT NULL
                  AND pc.symbol <> ''
                LIMIT 500
            """))).fetchall()
        return [r.symbol for r in rows if r.symbol]
    except Exception as exc:
        logger.warning("[gate-ws-leader] failed to resolve futures symbols: %s", exc)
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
        serve_started_ms: Optional[int] = None,
    ) -> None:
        self._redis = redis
        self._instance_id = instance_id
        self._on_lost = on_leadership_lost
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        # Used to debounce ``refresh_subscriptions()`` requests:
        # only requests strictly NEWER than the time we started serving
        # are honored. Set by ``_serve_as_leader`` right before starting
        # the renew loop; ``None`` disables the check.
        self._serve_started_ms = serve_started_ms

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
            if not still_leader:
                logger.warning(
                    "[gate-ws-leader] lost leadership (lock missing or owned by another instance) — stopping local WS"
                )
                try:
                    await self._on_lost()
                except Exception as exc:
                    logger.warning("[gate-ws-leader] on_lost callback failed: %s", exc)
                return

            # Cross-instance refresh trigger (Task #194).
            # Round-2 review (blocker 5): prefer an in-place subscription
            # diff so existing streams are not interrupted. Only fall
            # back to the destructive drop+reconnect if the in-place
            # path is unavailable (no client, no live socket) or fails.
            if await self._refresh_requested():
                if await self._try_in_place_refresh():
                    # Bump the watermark so the SAME refresh marker
                    # doesn't keep firing on every renewal tick.
                    import time as _time
                    self._serve_started_ms = int(_time.time() * 1000)
                    continue
                logger.info(
                    "[WS] refresh_subscriptions requested — in-place diff unavailable, "
                    "dropping WS to re-resolve symbols"
                )
                try:
                    await self._on_lost()
                except Exception as exc:
                    logger.warning("[gate-ws-leader] refresh on_lost failed: %s", exc)
                return

    async def _try_in_place_refresh(self) -> bool:
        """Attempt an in-place subscription diff against the live WS.

        Returns ``True`` on success, ``False`` to signal the caller to
        fall back to drop+reconnect. Never raises — any exception is
        downgraded to a warning + ``False``.
        """
        try:
            from ..websocket.gate_ws_client import get_gate_ws
            client = get_gate_ws()
            if client is None:
                return False
            spot_pairs, futures_contracts = await asyncio.gather(
                _resolve_spot_symbols(),
                _resolve_futures_symbols(),
            )
            await client.apply_subscription_diff(spot_pairs, futures_contracts)
            logger.info(
                "[WS] in-place subscription diff applied (spot=%d, futures=%d)",
                len(spot_pairs), len(futures_contracts),
            )
            return True
        except Exception as exc:
            logger.warning("[WS] in-place diff failed, will fall back: %s", exc)
            return False

    async def _refresh_requested(self) -> bool:
        if self._serve_started_ms is None:
            return False
        try:
            raw = await self._redis.get(REFRESH_REQUEST_KEY)
        except Exception as exc:
            logger.debug("[gate-ws-leader] refresh poll failed: %s", exc)
            return False
        if raw is None:
            return False
        try:
            ts = int(raw)
        except (TypeError, ValueError):
            return False
        return ts > self._serve_started_ms


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
        # Lazy import — keep this module decoupled from prometheus at
        # import time (the metrics module degrades to no-ops when
        # prometheus-client is missing, so this call is always safe).
        from .robust_indicators.metrics import set_ws_is_leader

        # Initialise the gauge to 0 so a freshly started reader replica
        # is observable immediately, before the first acquire attempt.
        set_ws_is_leader(False, self._instance_id)
        try:
            while not self._stopping.is_set():
                acquired = await _try_acquire_leader(self._redis, self._instance_id)
                if acquired:
                    self._is_leader = True
                    set_ws_is_leader(True, self._instance_id)
                    logger.info(
                        "[gate-ws-leader] acquired gate_ws:leader (instance=%s)",
                        self._instance_id,
                    )
                    try:
                        await self._serve_as_leader()
                    finally:
                        self._is_leader = False
                        set_ws_is_leader(False, self._instance_id)
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
        from ..websocket.event_handlers import register_all_handlers

        # ── ETAPA 1+2: resolve and log both market universes ──────────────
        spot_pairs, futures_contracts = await asyncio.gather(
            _resolve_spot_symbols(),
            _resolve_futures_symbols(),
        )

        logger.info("[POOL] SPOT symbols: %d", len(spot_pairs))
        logger.info("[POOL] FUTURES symbols: %d", len(futures_contracts))

        if not spot_pairs and not futures_contracts:
            logger.warning(
                "[gate-ws-leader] no symbols resolved for either market — "
                "releasing lock and going back to candidate mode"
            )
            await _release_leader(self._redis, self._instance_id)
            return

        api_key, api_secret = await _load_gate_credentials()

        try:
            # Pass ``register_handlers`` so the callback runs *before*
            # the receive tasks are spawned — otherwise the first few
            # frames off the wire could be dispatched against an empty
            # handler map and silently dropped.
            await _ws_client_mod.start_gate_ws(
                api_key=api_key,
                api_secret=api_secret,
                contracts=futures_contracts,
                spot_pairs=spot_pairs,
                instance_id=self._instance_id,
                register_handlers=register_all_handlers,
            )
            logger.info(
                "[gate-ws-leader] Gate WS started (instance=%s, spot_pairs=%d, futures_contracts=%d)",
                self._instance_id, len(spot_pairs), len(futures_contracts),
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

        runner = _LeaderRunner(
            self._redis,
            self._instance_id,
            _on_lost,
            serve_started_ms=int(time.time() * 1000),
        )
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


REDIS_BOOTSTRAP_RETRY_SECONDS = 30
"""How long to wait between retries when Redis is unavailable at startup.

Picked to align with ``redis_client.INIT_RETRY_COOLDOWN_SECONDS`` so we
don't hammer the singleton's own cooldown.
"""


async def start_gate_ws_with_leader_election() -> Optional[Callable[[], Awaitable[None]]]:
    """Start the supervisor and return a shutdown coroutine.

    Returns ``None`` only when the feature flag ``ENABLE_GATE_WS`` is
    not ``"1"`` — this is the explicit rollback switch and we honor it
    immediately.

    When the flag is on but Redis is unavailable at startup we do *not*
    give up for the lifetime of the process: a background bootstrap
    task retries ``get_async_redis()`` every
    ``REDIS_BOOTSTRAP_RETRY_SECONDS`` until either it succeeds (then it
    spawns the supervisor) or the returned shutdown coroutine cancels
    it.  This avoids the previous failure mode where a Cloud Run replica
    that booted seconds before Redis became reachable would stay idle
    forever.

    The returned shutdown coroutine MUST be awaited from the FastAPI
    lifespan ``finally``.  Once the supervisor is up, leadership
    behavior is unchanged: this replica may start out as a reader and
    become the leader later — when the current leader dies, its 30 s
    lock expires and the next ``_try_acquire_leader`` call wins within
    ~``CANDIDATE_POLL_INTERVAL_SECONDS``.
    """
    if os.environ.get("ENABLE_GATE_WS") != "1":
        logger.info("[gate-ws-leader] ENABLE_GATE_WS != 1 — Gate WS disabled (REST polling only)")
        return None

    instance_id = _resolve_instance_id()

    # Try Redis once synchronously — the happy path keeps the same
    # log shape as before.  If it fails we hand off to the bootstrap
    # task so the lifespan can finish initializing.
    redis = await get_async_redis()
    if redis is not None:
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

    # Redis unavailable at boot → background retry loop.
    logger.warning(
        "[gate-ws-leader] Redis unavailable at startup — retrying every %ds in the background",
        REDIS_BOOTSTRAP_RETRY_SECONDS,
    )
    state: dict = {"supervisor": None, "stopping": asyncio.Event()}

    async def _bootstrap_loop() -> None:
        while not state["stopping"].is_set():
            try:
                await asyncio.wait_for(
                    state["stopping"].wait(),
                    timeout=REDIS_BOOTSTRAP_RETRY_SECONDS,
                )
                return  # shutdown was requested before Redis came back
            except asyncio.TimeoutError:
                pass

            try:
                r = await get_async_redis()
            except Exception as exc:
                logger.warning("[gate-ws-leader] bootstrap: get_async_redis() raised: %s", exc)
                continue
            if r is None:
                continue

            sup = _GateWSSupervisor(r, instance_id)
            sup.start()
            state["supervisor"] = sup
            logger.info(
                "[gate-ws-leader] Redis recovered — supervisor started "
                "(instance=%s, candidate_poll=%ds, leader_ttl=%ds)",
                instance_id, CANDIDATE_POLL_INTERVAL_SECONDS, LEADER_TTL_SECONDS,
            )
            return

    bootstrap_task = asyncio.create_task(_bootstrap_loop(), name="gate_ws_redis_bootstrap")

    async def _shutdown() -> None:
        logger.info("[gate-ws-leader] lifespan shutdown — stopping bootstrap + supervisor")
        state["stopping"].set()
        # Stop the bootstrap loop first so it can exit cleanly.
        try:
            await asyncio.wait_for(bootstrap_task, timeout=5)
        except asyncio.TimeoutError:
            bootstrap_task.cancel()
            try:
                await bootstrap_task
            except (asyncio.CancelledError, Exception):
                pass
        except (asyncio.CancelledError, Exception):
            pass
        sup = state["supervisor"]
        if sup is not None:
            await sup.stop()

    return _shutdown


async def refresh_subscriptions(redis=None) -> Dict[str, Any]:
    """Request the active WS leader to re-resolve and re-subscribe symbols.

    Writes a millisecond timestamp to ``REFRESH_REQUEST_KEY`` in Redis.
    The active leader's ``_LeaderRunner`` polls that key on every renew
    tick (every :data:`LEADER_RENEW_INTERVAL_SECONDS` seconds) and, if
    the request is newer than the moment it started serving, drops the
    current WS so the supervisor re-acquires the lock, re-resolves the
    symbol universe from ``pool_coins`` and reconnects with the new
    subscription list.

    Cross-instance: any FastAPI replica may call this; the request is
    honored by whichever replica currently holds the leader lock.

    Returns
    -------
    dict
        ``{"requested": bool, "ts_ms": int, "reason": str}``.
        ``requested=False`` only when Redis is unavailable.
    """
    if redis is None:
        redis = await get_async_redis()
    if redis is None:
        return {"requested": False, "ts_ms": None, "reason": "redis_unavailable"}
    ts_ms = int(time.time() * 1000)
    try:
        await redis.set(
            REFRESH_REQUEST_KEY,
            str(ts_ms).encode("utf-8"),
            ex=REFRESH_REQUEST_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning("[WS] refresh_subscriptions write failed: %s", exc)
        return {"requested": False, "ts_ms": ts_ms, "reason": f"{type(exc).__name__}: {exc}"}
    return {"requested": True, "ts_ms": ts_ms, "reason": "ok"}


__all__ = [
    "LEADER_KEY",
    "LEADER_TTL_SECONDS",
    "LEADER_RENEW_INTERVAL_SECONDS",
    "CANDIDATE_POLL_INTERVAL_SECONDS",
    "REDIS_BOOTSTRAP_RETRY_SECONDS",
    "REFRESH_REQUEST_KEY",
    "REFRESH_REQUEST_TTL_SECONDS",
    "start_gate_ws_with_leader_election",
    "refresh_subscriptions",
    # Exported for tests:
    "_GateWSSupervisor",
    "_LeaderRunner",
    "_try_acquire_leader",
    "_renew_leader",
    "_release_leader",
    "_resolve_instance_id",
]
