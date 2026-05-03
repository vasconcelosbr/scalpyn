"""Gate.io WebSocket client — maintains authenticated connections to Gate.io WS APIs.

Futures WS: wss://fx-ws.gateio.ws/v4/ws/usdt
Spot WS:    wss://api.gateio.ws/ws/v4/
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import defaultdict
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)

# ── Gate.io endpoints ─────────────────────────────────────────────────────────
FUTURES_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
SPOT_WS_URL = "wss://api.gateio.ws/ws/v4/"

# ── Reconnect policy ──────────────────────────────────────────────────────────
RECONNECT_BASE_DELAY = 1.0   # seconds
RECONNECT_MAX_DELAY = 60.0   # seconds
RECONNECT_MAX_RETRIES = 20

# Ping interval Gate.io expects to keep the connection alive
PING_INTERVAL_SECONDS = 20


# ── Futures channels and their initial payload builders ───────────────────────
def _futures_channel_payloads(contracts: list[str]) -> list[tuple[str, list]]:
    """Return (channel, payload) pairs for all futures subscriptions.

    ``futures.trades`` feeds the Redis trade buffer (key prefix
    ``trades_buffer:futures:``) that backs real-time order-flow ingestion
    for futures contracts, mirroring the ``spot.trades`` path.
    """
    return [
        ("futures.tickers", contracts),
        ("futures.candlesticks", [f"1m,{c}" for c in contracts]),
        ("futures.trades", contracts),
        ("futures.positions", contracts),
        ("futures.orders", ["-1"]),           # -1 = all contracts
        ("futures.autoorders", ["-1"]),
        ("futures.liquidates", contracts),
    ]


def _spot_channel_payloads(spot_pairs: list[str]) -> list[tuple[str, list]]:
    """Return (channel, payload) pairs for all spot subscriptions.

    ``spot.trades`` (Task #171) feeds the Redis trade buffer that backs
    real-time order-flow ingestion, replacing the per-symbol REST polling
    loop in ``order_flow_service.get_order_flow_data``.
    """
    return [
        ("spot.tickers", spot_pairs),
        ("spot.orders", spot_pairs),
        ("spot.trades", spot_pairs),
    ]


class GateWSClient:
    """Maintains authenticated WebSocket connections to Gate.io for both futures
    and spot markets.

    Channels subscribed (futures): tickers, candlesticks (1m), positions,
        orders, autoorders, liquidates.
    Channels subscribed (spot): tickers, orders.

    Message dispatch:
        Each channel → one or more registered async handler coroutines,
        registered via ``register_handler(channel, coro)``.

    Reconnect policy:
        Never-die contract (Task #180): the per-market connection loop
        (``_run_with_backoff``) keeps retrying indefinitely while
        ``self._running`` is True — there is no kill-switch on the retry
        count.  Backoff is exponential starting at ``RECONNECT_BASE_DELAY``
        (1 s), doubling each attempt and capped at ``RECONNECT_MAX_DELAY``
        (60 s).  ``RECONNECT_MAX_RETRIES`` (20) is now only the cadence
        for the page-able ``CRITICAL`` log ("STILL RETRYING") so Sentry
        gets a fresh signal roughly every ``MAX_RETRIES * MAX_DELAY``
        seconds without storming on every individual retry — in-between
        retries log at WARN.  A clean return from the inner loop while
        still running resets both the attempt counter and the delay
        back to base.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        contracts: list[str],
        spot_pairs: list[str],
        instance_id: str = "default",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._contracts = contracts
        self._spot_pairs = spot_pairs
        # Label used by the ``gate_ws_connected{instance=…}`` Prometheus
        # gauge so multi-instance deployments can distinguish the leader
        # replica from any future reader replicas.
        self._instance_id = instance_id

        # channel → list of async handler coroutines
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

        self._running = False
        self._futures_task: Optional[asyncio.Task] = None
        self._spot_task: Optional[asyncio.Task] = None

        # Live WS handles, populated while connected. Used by
        # ``apply_subscription_diff`` to send subscribe/unsubscribe
        # frames in-place without dropping the connection. ``None``
        # means "no live socket right now" (between reconnects), in
        # which case the caller should fall back to drop+reconnect.
        self._spot_ws = None
        self._futures_ws = None
        # Single mutex serialises in-place diffs so two near-simultaneous
        # refresh ticks cannot interleave subscribe/unsubscribe frames.
        self._diff_lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the WS connections needed for the configured channels.

        Each market task is only spawned when there is something to
        subscribe to — passing ``contracts=[]`` skips the futures
        connection entirely and ``spot_pairs=[]`` skips the spot one.
        This matters for the Task #171 rollout, which is spot-only:
        without this guard we would still open an idle futures socket
        per replica that authenticates, sends pings forever, and adds
        no value.
        """
        if self._running:
            logger.warning("GateWSClient.start() called but client is already running")
            return

        if not self._contracts and not self._spot_pairs:
            logger.warning(
                "GateWSClient.start() called with no contracts and no spot_pairs — nothing to subscribe to"
            )
            return

        self._running = True

        markets: list[str] = []
        if self._contracts:
            self._futures_task = asyncio.create_task(
                self._run_with_backoff("futures", self._run_futures_ws),
                name="gate_ws_futures",
            )
            markets.append(f"futures({len(self._contracts)})")
        if self._spot_pairs:
            self._spot_task = asyncio.create_task(
                self._run_with_backoff("spot", self._run_spot_ws),
                name="gate_ws_spot",
            )
            markets.append(f"spot({len(self._spot_pairs)})")

        logger.info("GateWSClient starting markets: %s", ", ".join(markets))

    async def stop(self) -> None:
        """Gracefully stop all WS connections."""
        logger.info("GateWSClient stopping")
        self._running = False

        tasks = [t for t in (self._futures_task, self._spot_task) if t is not None]
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._futures_task = None
        self._spot_task = None
        logger.info("GateWSClient stopped")

    def register_handler(self, channel: str, handler: Callable) -> None:
        """Register an async handler coroutine for a channel.

        Multiple handlers per channel are supported; they are all called
        concurrently when a message arrives on that channel.
        """
        self._handlers[channel].append(handler)
        logger.debug("Registered handler %s for channel '%s'", handler.__name__, channel)

    # ── Internal: backoff wrapper ─────────────────────────────────────────────

    async def _run_with_backoff(self, market: str, coro_factory: Callable) -> None:
        """Run *coro_factory* with exponential backoff on failure.

        Never-die contract (Task #180): this loop MUST keep retrying
        indefinitely while ``self._running`` is True.  ``RECONNECT_MAX_RETRIES``
        is no longer a kill-switch — it is now only the cadence at which
        we emit a CRITICAL log so the on-call gets a fresh page roughly
        every ``MAX_RETRIES * RECONNECT_MAX_DELAY`` seconds (≈20 min at
        default settings) instead of being silenced after the first 20
        failures, while every individual retry stays at WARN level to
        avoid drowning Sentry.

        On a successful connection (``coro_factory`` returns without
        raising while still running), the attempt counter and backoff
        delay are reset so the next reconnect starts fast — mirroring
        how a fresh process would behave.
        """
        attempt = 0
        delay = RECONNECT_BASE_DELAY

        while self._running:
            try:
                await coro_factory()
                # If coro returns normally (e.g. stop() was called), exit.
                if not self._running:
                    break
                # Unexpected clean exit — treat as a transient drop, reset
                # the backoff so we reconnect immediately next loop, and
                # log at WARN.
                logger.warning(
                    "[%s] WS exited cleanly but still running — reconnecting",
                    market,
                )
                attempt = 0
                delay = RECONNECT_BASE_DELAY
            except asyncio.CancelledError:
                logger.info("[%s] WS task cancelled", market)
                return
            except Exception as exc:
                attempt += 1

                # Page on every multiple of MAX_RETRIES (20, 40, 60, …)
                # so we keep a CRITICAL signal in Sentry without storming
                # it on every single retry.
                if attempt % RECONNECT_MAX_RETRIES == 0:
                    logger.critical(
                        "[%s] WS failed %d times — STILL RETRYING. Last error: %s",
                        market, attempt, exc,
                    )
                else:
                    logger.warning(
                        "[%s] WS lost (attempt %d): %s — retry in %.1fs",
                        market, attempt, exc, delay,
                    )

            if not self._running:
                break

            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

        logger.info("[%s] WS backoff loop exited", market)

    # ── Internal: connection loops ────────────────────────────────────────────

    async def _run_futures_ws(self) -> None:
        """Connect, authenticate, subscribe futures channels, and dispatch messages."""
        from ..services.robust_indicators.metrics import set_ws_connected, set_ws_auth_ok

        logger.info("Connecting to Gate.io Futures WS: %s", FUTURES_WS_URL)

        async with websockets.connect(
            FUTURES_WS_URL,
            ping_interval=None,   # We handle pings manually
            ping_timeout=None,
            close_timeout=10,
        ) as ws:
            logger.info("Futures WS connected")
            # Expose for in-place subscription diff. Cleared in finally.
            self._futures_ws = ws

            auth_ok = await self._send_auth(ws, "futures")
            # Two distinct gauges (Task #171):
            #   • gate_ws_connected = transport health (handshake done +
            #     subscriptions about to be sent) — public channels keep
            #     streaming even when auth fails, so this MUST be set
            #     to 1 regardless of auth_ok.
            #   • gate_ws_auth_ok = auth-specific health.
            # The transport gauge is reset to 0 in ``finally`` so a
            # forced disconnect surfaces immediately in Prometheus.
            set_ws_connected("futures", True, instance=self._instance_id)
            set_ws_auth_ok("futures", auth_ok, instance=self._instance_id)

            for channel, payload in _futures_channel_payloads(self._contracts):
                await self._subscribe(ws, channel, payload)

            ping_task = asyncio.create_task(
                self._ping_loop(ws, "futures"),
                name="gate_ws_futures_ping",
            )
            try:
                await self._receive_loop(ws)
            finally:
                self._futures_ws = None
                set_ws_connected("futures", False, instance=self._instance_id)
                set_ws_auth_ok("futures", False, instance=self._instance_id)
                ping_task.cancel()
                await asyncio.gather(ping_task, return_exceptions=True)

    async def _run_spot_ws(self) -> None:
        """Connect, authenticate, subscribe spot channels, and dispatch messages."""
        from ..services.robust_indicators.metrics import set_ws_connected, set_ws_auth_ok

        logger.info("Connecting to Gate.io Spot WS: %s", SPOT_WS_URL)

        async with websockets.connect(
            SPOT_WS_URL,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=10,
        ) as ws:
            logger.info("Spot WS connected")
            self._spot_ws = ws

            auth_ok = await self._send_auth(ws, "spot")
            # See _run_futures_ws — same two-gauge contract.
            set_ws_connected("spot", True, instance=self._instance_id)
            set_ws_auth_ok("spot", auth_ok, instance=self._instance_id)

            for channel, payload in _spot_channel_payloads(self._spot_pairs):
                await self._subscribe(ws, channel, payload)

            ping_task = asyncio.create_task(
                self._ping_loop(ws, "spot"),
                name="gate_ws_spot_ping",
            )
            try:
                await self._receive_loop(ws)
            finally:
                self._spot_ws = None
                set_ws_connected("spot", False, instance=self._instance_id)
                set_ws_auth_ok("spot", False, instance=self._instance_id)
                ping_task.cancel()
                await asyncio.gather(ping_task, return_exceptions=True)

    # ── Internal: receive loop ────────────────────────────────────────────────

    async def _receive_loop(self, ws) -> None:
        """Read messages from the WS and dispatch them to registered handlers."""
        async for raw in ws:
            if not self._running:
                break
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning("WS received non-JSON frame: %s — %s", raw[:120], exc)
                continue

            await self._dispatch(message)

    # ── Internal: ping loop ───────────────────────────────────────────────────

    async def _ping_loop(self, ws, market: str) -> None:
        """Send a ping every PING_INTERVAL_SECONDS to keep the connection alive."""
        channel = f"{market}.ping"
        try:
            while True:
                await asyncio.sleep(PING_INTERVAL_SECONDS)
                if not self._running:
                    break
                ping_msg = {"time": int(time.time()), "channel": channel}
                try:
                    await ws.send(json.dumps(ping_msg))
                    logger.debug("[%s] Ping sent", market)
                except (ConnectionClosed, WebSocketException) as exc:
                    logger.warning("[%s] Ping failed (connection closed): %s", market, exc)
                    break
        except asyncio.CancelledError:
            pass

    # ── Internal: auth & subscribe ────────────────────────────────────────────

    async def _send_auth(self, ws, market: str) -> bool:
        """Send Gate.io WS authentication message.

        Returns ``True`` when:
          * Gate replied with ``result.status == "success"``, **or**
          * the client has no API credentials (``api_key`` empty) — this
            is the unauthenticated mode used by the order-flow ingestion
            path which only needs the public ``spot.trades`` channel.

        Returns ``False`` when the auth handshake actually failed
        (timeout, error response, exception).  The caller is responsible
        for not setting the ``gate_ws_connected`` gauge in that case.
        """
        # Skip the handshake entirely when running unauthenticated; Gate
        # does not require login for public channels and a bogus signature
        # would just earn an error frame.
        if not self._api_key or not self._api_secret:
            logger.info("[%s] no API credentials configured — skipping WS auth (public channels only)", market)
            return True

        ts = int(time.time())
        channel = f"{market}.login"
        event = "api"

        sign_body = f"channel={channel}&event={event}&time={ts}"
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            sign_body.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        auth_msg = {
            "time": ts,
            "channel": channel,
            "event": event,
            "payload": {
                "api_key": self._api_key,
                "timestamp": str(ts),
                "signature": signature,
            },
        }

        await ws.send(json.dumps(auth_msg))
        logger.debug("[%s] Auth message sent (ts=%d)", market, ts)

        # Wait briefly for the auth response
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            resp = json.loads(raw)
            result = resp.get("result", {})
            if isinstance(result, dict) and result.get("status") == "success":
                logger.info("[%s] WS authentication successful", market)
                return True
            logger.warning("[%s] WS authentication response: %s", market, resp)
            return False
        except asyncio.TimeoutError:
            logger.warning("[%s] No auth response within 10s — treating as failure", market)
            return False
        except Exception as exc:
            logger.warning("[%s] Error reading auth response: %s", market, exc)
            return False

    async def _subscribe(self, ws, channel: str, payload: list) -> None:
        """Send a subscribe message for the given channel and payload."""
        await self._send_sub_event(ws, channel, payload, event="subscribe")

    async def _unsubscribe(self, ws, channel: str, payload: list) -> None:
        """Send an unsubscribe message for the given channel and payload."""
        await self._send_sub_event(ws, channel, payload, event="unsubscribe")

    async def _send_sub_event(self, ws, channel: str, payload: list, *, event: str) -> None:
        msg = {
            "time": int(time.time()),
            "channel": channel,
            "event": event,
            "payload": payload,
        }
        try:
            await ws.send(json.dumps(msg))
            logger.debug("[%s] channel='%s' payload=%s", event, channel, payload)
        except Exception as exc:
            logger.error("Failed to %s channel '%s': %s", event, channel, exc)
            raise

    # ── Public: in-place subscription diff ────────────────────────────────────

    async def apply_subscription_diff(
        self,
        spot_pairs: list[str],
        futures_contracts: list[str],
    ) -> dict:
        """Reconcile the live subscription set against ``spot_pairs`` /
        ``futures_contracts`` without dropping the WebSocket connection.

        Sends ``subscribe`` for newly-added symbols and ``unsubscribe``
        for removed ones; symbols already subscribed are untouched so
        existing streams are not interrupted.

        Returns ``{"spot": {"added": N, "removed": N},
        "futures": {"added": N, "removed": N}}``.

        Raises ``RuntimeError`` when the affected market has no live
        socket — callers must fall back to drop+reconnect in that case
        so the system never silently loses its subscriptions.
        """
        async with self._diff_lock:
            spot_added = sorted(set(spot_pairs) - set(self._spot_pairs))
            spot_removed = sorted(set(self._spot_pairs) - set(spot_pairs))
            fut_added = sorted(set(futures_contracts) - set(self._contracts))
            fut_removed = sorted(set(self._contracts) - set(futures_contracts))

            spot_changes = bool(spot_added or spot_removed)
            fut_changes = bool(fut_added or fut_removed)

            if spot_changes and self._spot_ws is None:
                raise RuntimeError("spot WS not connected — cannot apply in-place diff")
            if fut_changes and self._futures_ws is None:
                raise RuntimeError("futures WS not connected — cannot apply in-place diff")

            # Use the helpers to derive the channel-specific payload
            # form for the added / removed subset (e.g. futures.candlesticks
            # expects ``["1m,<contract>", ...]``, not raw symbols). Skip
            # universe-wide channels whose payload sentinel is ``["-1"]``
            # — those subscriptions cover all contracts already and must
            # not be re-sent on diff.
            def _is_universe_channel(payload: list) -> bool:
                return payload == ["-1"]

            if spot_added:
                for channel, payload in _spot_channel_payloads(spot_added):
                    if _is_universe_channel(payload):
                        continue
                    await self._subscribe(self._spot_ws, channel, payload)
            if spot_removed:
                for channel, payload in _spot_channel_payloads(spot_removed):
                    if _is_universe_channel(payload):
                        continue
                    await self._unsubscribe(self._spot_ws, channel, payload)
            if fut_added:
                for channel, payload in _futures_channel_payloads(fut_added):
                    if _is_universe_channel(payload):
                        continue
                    await self._subscribe(self._futures_ws, channel, payload)
            if fut_removed:
                for channel, payload in _futures_channel_payloads(fut_removed):
                    if _is_universe_channel(payload):
                        continue
                    await self._unsubscribe(self._futures_ws, channel, payload)

            # Update the desired-state lists ONLY after the WS frames
            # are flushed so a mid-flight error leaves the in-memory
            # state matching what the broker actually received.
            self._spot_pairs = list(spot_pairs)
            self._contracts = list(futures_contracts)

            logger.info(
                "[WS] apply_subscription_diff spot(+%d/-%d) futures(+%d/-%d)",
                len(spot_added), len(spot_removed),
                len(fut_added), len(fut_removed),
            )
            return {
                "spot": {"added": len(spot_added), "removed": len(spot_removed)},
                "futures": {"added": len(fut_added), "removed": len(fut_removed)},
            }

    # ── Internal: dispatch ────────────────────────────────────────────────────

    async def _dispatch(self, message: dict) -> None:
        """Route an incoming WS message to registered handlers."""
        channel: str = message.get("channel", "")
        event: str = message.get("event", "")
        result = message.get("result")

        # Subscription confirmation
        if event == "subscribe":
            if isinstance(result, dict):
                if result.get("status") == "success":
                    logger.info("Subscription confirmed: channel='%s'", channel)
                else:
                    logger.warning("Subscription failed: channel='%s', result=%s", channel, result)
            return

        # Pong response
        if event == "pong" or channel.endswith(".pong"):
            logger.debug("Pong received on channel '%s'", channel)
            return

        # Auth confirmation
        if channel.endswith(".login"):
            return

        # Error frame
        if "error" in message and message["error"] is not None:
            logger.error("WS error on channel '%s': %s", channel, message["error"])
            return

        # Data update — dispatch to registered handlers
        if event == "update" and result is not None:
            handlers = self._handlers.get(channel, [])
            if not handlers:
                logger.debug("No handlers registered for channel '%s'", channel)
                return

            # Normalise: some channels return a single dict, wrap it
            if isinstance(result, dict):
                result = [result]

            coros = [h(result) for h in handlers]
            results = await asyncio.gather(*coros, return_exceptions=True)
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.error(
                        "Handler %s for channel '%s' raised: %s",
                        handlers[i].__name__, channel, res,
                    )
            return

        logger.debug("Unhandled WS frame: channel='%s' event='%s'", channel, event)


# ── Module-level singleton ────────────────────────────────────────────────────

_global_client: Optional[GateWSClient] = None


async def start_gate_ws(
    api_key: str,
    api_secret: str,
    contracts: list[str],
    spot_pairs: list[str],
    instance_id: str = "default",
    register_handlers: Optional[Callable[["GateWSClient"], None]] = None,
) -> GateWSClient:
    """Create and start the global GateWSClient singleton.

    If a client is already running it is returned unchanged.

    ``instance_id`` is forwarded to the Prometheus
    ``gate_ws_connected{instance=…}`` gauge so multi-instance deployments
    can distinguish the leader from any reader replicas.

    ``register_handlers`` is called synchronously **before** the receive
    tasks are spawned so the very first frame off the wire is dispatched
    to a registered handler — closing the startup race where the WS task
    could begin reading messages before the caller had a chance to wire
    its handlers.
    """
    global _global_client

    if _global_client is not None:
        logger.warning("start_gate_ws() called but a client is already running — returning existing client")
        return _global_client

    client = GateWSClient(
        api_key=api_key,
        api_secret=api_secret,
        contracts=contracts,
        spot_pairs=spot_pairs,
        instance_id=instance_id,
    )
    if register_handlers is not None:
        try:
            register_handlers(client)
        except Exception as exc:
            logger.error("[gate-ws] register_handlers callback failed: %s", exc, exc_info=True)
            raise
    await client.start()
    _global_client = client
    logger.info("Global GateWSClient started (instance=%s)", instance_id)
    return client


async def stop_gate_ws() -> None:
    """Stop and discard the global GateWSClient singleton."""
    global _global_client

    if _global_client is None:
        logger.warning("stop_gate_ws() called but no client is running")
        return

    await _global_client.stop()
    _global_client = None
    logger.info("Global GateWSClient stopped and cleared")


def get_gate_ws() -> Optional[GateWSClient]:
    """Return the running global GateWSClient, or None if not started."""
    return _global_client
