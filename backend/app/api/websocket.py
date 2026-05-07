"""WebSocket API — real-time price, score, and signal streaming."""

import asyncio
import json
import logging
from typing import Dict, Set
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])


class ConnectionManager:
    """Manages WebSocket connections grouped by channel."""

    def __init__(self):
        self.connections: Dict[str, Set[WebSocket]] = {
            "market": set(),
            "signals": set(),
            "trades": set(),
            "decisions": set(),
        }

    async def connect(self, websocket: WebSocket, channel: str):
        await websocket.accept()
        if channel not in self.connections:
            self.connections[channel] = set()
        self.connections[channel].add(websocket)
        logger.info(f"WS connected: channel={channel}, total={len(self.connections[channel])}")

    def disconnect(self, websocket: WebSocket, channel: str):
        if channel in self.connections:
            self.connections[channel].discard(websocket)
        logger.info(f"WS disconnected: channel={channel}")

    async def broadcast(self, channel: str, data: dict):
        if channel not in self.connections:
            return
        dead = set()
        for ws in self.connections[channel]:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.connections[channel].discard(ws)


manager = ConnectionManager()


@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    """Stream real-time market data (prices + scores)."""
    await manager.connect(websocket, "market")
    try:
        while True:
            # Keep connection alive, broadcast handled by tasks
            data = await websocket.receive_text()
            # Client can send ping/subscribe messages
            if data == "ping":
                await websocket.send_json({"type": "pong", "ts": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        manager.disconnect(websocket, "market")


@router.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket):
    """Stream real-time trade signals."""
    await manager.connect(websocket, "signals")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, "signals")


@router.websocket("/ws/trades")
async def ws_trades(websocket: WebSocket):
    """Stream real-time trade execution updates."""
    await manager.connect(websocket, "trades")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, "trades")


@router.websocket("/ws/decisions")
async def ws_decisions(websocket: WebSocket):
    """Stream real-time decision log updates.

    Task #234 hotfix — degrade mode: when the connect/loop raises an
    unexpected exception (broker unreachable, manager registry corrupted,
    etc.) we send a single ``{"status":"degraded"}`` frame and close the
    socket cleanly with code 1011. The previous implementation surfaced
    a 503 to the client, which the frontend treated as a hard failure
    and disabled the entire decision log panel for the session. Degraded
    mode lets the UI show a yellow indicator instead of going dark.
    """
    accepted = False
    try:
        await manager.connect(websocket, "decisions")
        accepted = True
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning(
            "[WS /ws/decisions] degraded — %s: %s",
            type(exc).__name__, exc,
        )
        if accepted:
            try:
                await websocket.send_json({
                    "type": "status",
                    "status": "degraded",
                    "reason": type(exc).__name__,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                pass
            try:
                await websocket.close(code=1011)
            except Exception:
                pass
        else:
            # Could not even accept; let the client retry rather than 503.
            try:
                await websocket.accept()
                await websocket.send_json({
                    "type": "status",
                    "status": "degraded",
                    "reason": type(exc).__name__,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                await websocket.close(code=1011)
            except Exception:
                pass
    finally:
        if accepted:
            manager.disconnect(websocket, "decisions")


async def broadcast_price_update(symbol: str, price: float, change_24h: float, score: float):
    """Called by market data tasks to push updates to connected clients."""
    await manager.broadcast("market", {
        "type": "price_update",
        "symbol": symbol,
        "price": price,
        "change_24h": change_24h,
        "score": score,
        "ts": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_signal(symbol: str, direction: str, score: float, details: dict):
    """Called by signal engine when a new signal is generated."""
    await manager.broadcast("signals", {
        "type": "signal",
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "details": details,
        "ts": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_trade_event(event_type: str, trade_data: dict):
    """Called by execution engine on trade events."""
    await manager.broadcast("trades", {
        "type": event_type,
        **trade_data,
        "ts": datetime.now(timezone.utc).isoformat(),
    })


async def broadcast_decision_created(decision_data: dict):
    """Called by the pipeline to push a new persisted decision."""
    await manager.broadcast("decisions", {
        "type": "decision.created",
        "data": decision_data,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
