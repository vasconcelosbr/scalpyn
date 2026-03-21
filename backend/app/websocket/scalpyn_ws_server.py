"""
Scalpyn WebSocket server — extended channels for Trading Desk.

New channels beyond what api/websocket.py already has:
  - "positions"  : real-time P&L updates for open positions (spot + futures)
  - "alerts"     : engine alerts (TP hits, SL hits, anti-liq warnings, emergencies)
  - "engine"     : engine status updates (running/paused, scan results)
  - "macro"      : macro regime updates

Relies on the existing ConnectionManager singleton from api/websocket.py.
New channels are registered on module import via setdefault so the
broadcast helpers in this module share the same socket pool as the core
channels (market, signals, trades).

All WS endpoints follow the same auth handshake pattern:
  1. Accept the connection.
  2. Wait for the first client frame: JSON ``{"user_id": "<uuid>"}``
  3. Send ``{"type": "connected", "channel": "..."}`` confirmation.
  4. Enter the main receive loop — handle "ping" frames, discard the rest.
  5. On disconnect: clean up via manager.disconnect().

No token validation is performed here; the server relies on same-origin
policy enforced by the CORS middleware in main.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..api.websocket import broadcast_trade_event, manager  # noqa: F401 — re-export

logger = logging.getLogger(__name__)

# ── Register the new channels on the shared manager ──────────────────────────
for _channel in ("positions", "alerts", "engine", "macro"):
    manager.connections.setdefault(_channel, set())

# ── Router ────────────────────────────────────────────────────────────────────
router = APIRouter(tags=["WebSocket Extended"])


# ── Internal helpers ──────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _accept_and_identify(websocket: WebSocket, channel: str) -> str | None:
    """
    Accept the WS connection, add it to the manager, then wait for the
    client's first message carrying ``{"user_id": "..."}``.

    Returns the user_id string on success, or None if the client
    disconnected / sent invalid data during the handshake.
    """
    await manager.connect(websocket, channel)
    try:
        raw = await websocket.receive_text()
        payload = json.loads(raw)
        user_id: str = payload.get("user_id", "")
    except (WebSocketDisconnect, json.JSONDecodeError, Exception) as exc:
        logger.warning("WS channel=%s handshake failed: %s", channel, exc)
        manager.disconnect(websocket, channel)
        return None

    await websocket.send_json({
        "type": "connected",
        "channel": channel,
        "user_id": user_id,
        "ts": _now(),
    })
    logger.info("WS Trading Desk connected: channel=%s user=%s", channel, user_id)
    return user_id


# ── Broadcast helpers ─────────────────────────────────────────────────────────

async def broadcast_position_update(user_id: str, position_data: dict[str, Any]) -> None:
    """
    Broadcast a real-time P&L update for a single open position.

    Tags the message with ``user_id`` so the frontend can filter messages
    for the authenticated user when the channel is shared.

    Args:
        user_id:       UUID string of the owning user.
        position_data: Dict with at minimum ``symbol``, ``unrealized_pnl``,
                       ``unrealized_pnl_pct``, ``current_price``,
                       ``market_type`` (spot | futures).
    """
    await manager.broadcast("positions", {
        "type": "position_update",
        "user_id": user_id,
        **position_data,
        "ts": _now(),
    })


async def broadcast_alert(
    user_id: str,
    alert_type: str,
    details: dict[str, Any],
) -> None:
    """
    Broadcast an engine alert to all clients subscribed to the "alerts" channel.

    Supported ``alert_type`` values (not exhaustive):
      - TP_HIT          : take-profit level reached
      - SL_HIT          : stop-loss triggered
      - EMERGENCY       : emergency engine action taken
      - ANTI_LIQ_WARNING: position approaching liquidation (alert zone)
      - ANTI_LIQ_CRITICAL: position in critical zone, reduction in progress
      - FUNDING_DRAIN   : cumulative funding cost exceeding configured limit

    Args:
        user_id:    UUID string of the affected user.
        alert_type: One of the values listed above (uppercase string).
        details:    Contextual data — symbol, price, distance_pct, etc.
    """
    await manager.broadcast("alerts", {
        "type": "alert",
        "alert_type": alert_type,
        "user_id": user_id,
        "details": details,
        "ts": _now(),
    })


async def broadcast_engine_status(
    user_id: str,
    profile: str,
    status: dict[str, Any],
) -> None:
    """
    Broadcast an engine lifecycle event.

    Args:
        user_id:  UUID string of the user whose engine changed state.
        profile:  "spot" | "futures"
        status:   Dict describing the new state, e.g.
                  ``{"state": "running", "positions": 3, "last_scan_at": "..."}``.
    """
    await manager.broadcast("engine", {
        "type": "engine_status",
        "user_id": user_id,
        "profile": profile,
        "status": status,
        "ts": _now(),
    })


async def broadcast_macro_update(
    regime: str,
    score: float,
    components: dict[str, Any],
) -> None:
    """
    Broadcast a macro regime change to ALL connected clients (no user filter).

    Macro state is global — it is not per-user.

    Args:
        regime:     One of STRONG_RISK_ON | RISK_ON | NEUTRAL | RISK_OFF | STRONG_RISK_OFF.
        score:      Composite macro score (0-100).
        components: Dict of per-component scores, e.g.
                    ``{"btc_trend": 72.0, "funding_rate_market": 55.0, ...}``.
    """
    await manager.broadcast("macro", {
        "type": "macro_update",
        "regime": regime,
        "score": score,
        "components": components,
        "ts": _now(),
    })


# ── WebSocket route handlers ──────────────────────────────────────────────────

@router.websocket("/ws/positions")
async def ws_positions(websocket: WebSocket) -> None:
    """
    Real-time position P&L updates for open spot and futures positions.

    Handshake:
      Client → ``{"user_id": "<uuid>"}``
      Server → ``{"type": "connected", "channel": "positions", ...}``

    Subsequent server pushes carry ``type="position_update"`` with P&L data.
    Client may send ``"ping"`` at any time; server responds with ``pong``.
    """
    user_id = await _accept_and_identify(websocket, "positions")
    if user_id is None:
        return

    try:
        while True:
            data = await websocket.receive_text()
            if data.strip() == "ping":
                await websocket.send_json({"type": "pong", "ts": _now()})
            # All other client frames are silently discarded;
            # positions channel is server-push only.
    except WebSocketDisconnect:
        manager.disconnect(websocket, "positions")
        logger.info("WS positions disconnected: user=%s", user_id)
    except Exception as exc:
        logger.warning("WS positions error for user=%s: %s", user_id, exc)
        manager.disconnect(websocket, "positions")


@router.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket) -> None:
    """
    Real-time alerts: TP hits, SL hits, emergency closes, anti-liq warnings,
    funding drain notifications.

    Handshake:
      Client → ``{"user_id": "<uuid>"}``
      Server → ``{"type": "connected", "channel": "alerts", ...}``

    Subsequent server pushes carry ``type="alert"`` with ``alert_type`` and
    ``details`` fields.  Client may send ``"ping"``; server responds with ``pong``.
    """
    user_id = await _accept_and_identify(websocket, "alerts")
    if user_id is None:
        return

    try:
        while True:
            data = await websocket.receive_text()
            if data.strip() == "ping":
                await websocket.send_json({"type": "pong", "ts": _now()})
    except WebSocketDisconnect:
        manager.disconnect(websocket, "alerts")
        logger.info("WS alerts disconnected: user=%s", user_id)
    except Exception as exc:
        logger.warning("WS alerts error for user=%s: %s", user_id, exc)
        manager.disconnect(websocket, "alerts")


@router.websocket("/ws/engine")
async def ws_engine(websocket: WebSocket) -> None:
    """
    Real-time engine status updates: started, paused, resumed, stopped,
    scan complete, and per-scan result summaries.

    Handshake:
      Client → ``{"user_id": "<uuid>"}``
      Server → ``{"type": "connected", "channel": "engine", ...}``

    Subsequent server pushes carry ``type="engine_status"`` with ``profile``
    (spot | futures) and ``status`` fields.
    Client may send ``"ping"``; server responds with ``pong``.
    """
    user_id = await _accept_and_identify(websocket, "engine")
    if user_id is None:
        return

    try:
        while True:
            data = await websocket.receive_text()
            if data.strip() == "ping":
                await websocket.send_json({"type": "pong", "ts": _now()})
    except WebSocketDisconnect:
        manager.disconnect(websocket, "engine")
        logger.info("WS engine disconnected: user=%s", user_id)
    except Exception as exc:
        logger.warning("WS engine error for user=%s: %s", user_id, exc)
        manager.disconnect(websocket, "engine")
