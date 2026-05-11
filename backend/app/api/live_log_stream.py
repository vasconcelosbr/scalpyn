"""Live operational endpoints — SSE log stream + balance/positions polls.

Three additive read-only endpoints designed for the operator UI:

* ``GET /api/live/log-stream``  — Server-Sent Events (SSE) feed of
  ``trade_decisions`` rows as they're written, fed by the in-process
  ``app.core.decision_event_bus``. Authenticated tenancy: each client
  only sees events for ``user_id == <authenticated caller>``.
* ``GET /api/live/balance``     — current spot USDT balance + capital
  locked in open positions (sum of ``trades.invested_value``).
* ``GET /api/live/positions``   — open positions enriched with the
  latest ``market_metadata.price`` and progress to take-profit.

All three require JWT auth (``get_current_user_id``). The SSE endpoint
also filters server-side by the optional ``market_type`` and ``status``
query parameters.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.decision_event_bus import redis_event_stream
from ..database import get_db
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["Live Diagnostics"])

# How long the SSE generator waits on the queue before emitting a
# heartbeat keep-alive so intermediate proxies / load balancers don't
# kill the connection. Heartbeats are SSE comments (``: ...\n\n``)
# which clients ignore at the EventSource API level.
_HEARTBEAT_INTERVAL_S = 15.0
_QUEUE_POLL_TIMEOUT_S = 1.0

# Statuses we accept on the SSE filter param; anything else is ignored
# (the stream still works, just unfiltered on that axis).
_ALLOWED_STATUSES = {"APPROVED", "REJECTED", "SKIPPED", "BLOCKED"}
_ALLOWED_MARKETS = {"spot", "futures"}

# Trade row statuses considered "currently open" — we accept both the
# legacy lowercase ``open`` (spot execution_engine) and the futures
# ``ACTIVE`` writer convention so the response covers every live
# position regardless of which engine opened it.
_OPEN_STATUSES = ("open", "ACTIVE")


@router.get("/log-stream")
async def log_stream(
    market_type: Optional[str] = Query(None, description="spot | futures"),
    status: Optional[str] = Query(None, description="APPROVED | REJECTED | SKIPPED"),
    caller_id: UUID = Depends(get_current_user_id),
) -> StreamingResponse:
    """Stream decision-audit events as Server-Sent Events.

    Tenancy is enforced server-side: only events whose payload
    ``user_id`` matches the authenticated caller are forwarded. Events
    from other tenants share the same in-process bus but never leave
    this generator. A heartbeat comment is emitted every ~15 s so
    proxies don't time out the connection during quiet periods.
    """
    market_filter = market_type if market_type in _ALLOWED_MARKETS else None
    status_filter = (
        status.upper() if (status and status.upper() in _ALLOWED_STATUSES) else None
    )
    caller_str = str(caller_id)

    async def event_gen() -> AsyncGenerator[bytes, None]:
        last_emit = time.monotonic()
        # Wrap the Redis subscriber generator in a task-friendly poll
        # loop so we can interleave heartbeats with message delivery.
        # Using ``__anext__`` + ``wait_for`` gives us a per-iteration
        # timeout; if the broker is silent we still emit keep-alives.
        stream = redis_event_stream()
        try:
            yield b": connected\n\n"
            while True:
                try:
                    event: Dict[str, Any] = await asyncio.wait_for(
                        stream.__anext__(), timeout=_QUEUE_POLL_TIMEOUT_S
                    )
                except asyncio.TimeoutError:
                    if time.monotonic() - last_emit >= _HEARTBEAT_INTERVAL_S:
                        yield b": heartbeat\n\n"
                        last_emit = time.monotonic()
                    continue
                except StopAsyncIteration:
                    # Subscriber generator exited (shouldn't happen
                    # without cancellation, but be defensive).
                    break

                # ── tenancy (HARD): drop events without user_id and
                # events for any other tenant. We never forward
                # ambiguous events to avoid a cross-tenant leak if a
                # future code path forgets to populate user_id.
                evt_user = event.get("user_id")
                if not evt_user or str(evt_user) != caller_str:
                    continue
                if market_filter and event.get("market_type") != market_filter:
                    continue
                if status_filter and event.get("status") != status_filter:
                    continue

                payload = json.dumps(event, default=str, ensure_ascii=False)
                yield f"data: {payload}\n\n".encode("utf-8")
                last_emit = time.monotonic()
        except asyncio.CancelledError:
            raise
        finally:
            try:
                await stream.aclose()
            except Exception:
                pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Balance ──────────────────────────────────────────────────────────


@router.get("/balance")
async def get_balance(
    db: AsyncSession = Depends(get_db),
    caller_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Return current available USDT plus capital locked in open trades.

    ``available_usdt`` comes from Gate.io via the user's stored
    ``ExchangeConnection`` (decrypted on demand). ``in_positions`` is
    the sum of ``invested_value`` for trades the caller currently has
    open. ``total = available_usdt + in_positions``.
    """
    from ..exchange_adapters.gate_adapter import GateAdapter
    from ..models.exchange_connection import ExchangeConnection
    from ..utils.encryption import decrypt

    available_usdt = 0.0
    source = "no_connection"
    error: Optional[str] = None

    exc_res = await db.execute(
        select(ExchangeConnection).where(
            ExchangeConnection.user_id == caller_id,
            ExchangeConnection.is_active == True,  # noqa: E712 — SQLA
        ).limit(1)
    )
    exc_row = exc_res.scalars().first()

    if exc_row is not None:
        try:
            raw_key = (
                bytes(exc_row.api_key_encrypted)
                if isinstance(exc_row.api_key_encrypted, memoryview)
                else exc_row.api_key_encrypted
            )
            raw_secret = (
                bytes(exc_row.api_secret_encrypted)
                if isinstance(exc_row.api_secret_encrypted, memoryview)
                else exc_row.api_secret_encrypted
            )
            adapter = GateAdapter(decrypt(raw_key).strip(), decrypt(raw_secret).strip())
            spot_accounts = await adapter.get_spot_balance()
            available_usdt = next(
                (
                    float(a.get("available", 0))
                    for a in spot_accounts
                    if a.get("currency") == "USDT"
                ),
                0.0,
            )
            source = "exchange"
        except Exception as exc:  # noqa: BLE001 — defensive surface
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("live/balance: adapter call failed: %s", error)
            source = "exchange_error"

    pos_q = await db.execute(
        text(
            """
            SELECT COALESCE(SUM(invested_value), 0) AS total_in_positions
              FROM trades
             WHERE user_id = :uid
               AND status = ANY(:open_statuses)
            """
        ).bindparams(),
        {"uid": str(caller_id), "open_statuses": list(_OPEN_STATUSES)},
    )
    in_positions = float(pos_q.scalar() or 0)

    return {
        "available_usdt": round(available_usdt, 8),
        "in_positions": round(in_positions, 8),
        "total": round(available_usdt + in_positions, 8),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "error": error,
    }


# ── Positions ────────────────────────────────────────────────────────


@router.get("/positions")
async def get_positions(
    db: AsyncSession = Depends(get_db),
    caller_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """List the caller's open trades enriched with latest price + TP progress.

    ``current_price`` is sourced from ``market_metadata.price`` (the
    same column already kept fresh by the collector pipeline — no
    separate exchange call from this endpoint). ``margin_to_target_pct``
    measures progress from entry to take-profit on a 0..100 scale and
    is clamped at both ends so a price overshoot beyond TP doesn't
    render as ``120%``.
    """
    rows = await db.execute(
        text(
            """
            SELECT t.id              AS id,
                   t.symbol          AS symbol,
                   t.entry_price     AS entry_price,
                   t.quantity        AS quantity,
                   t.invested_value  AS invested_value,
                   t.take_profit_price AS take_profit_price,
                   t.status          AS status,
                   mm.price          AS current_price
              FROM trades t
              LEFT JOIN market_metadata mm ON mm.symbol = t.symbol
             WHERE t.user_id = :uid
               AND t.status = ANY(:open_statuses)
             ORDER BY t.id DESC
            """
        ).bindparams(),
        {"uid": str(caller_id), "open_statuses": list(_OPEN_STATUSES)},
    )

    items = []
    for r in rows.fetchall():
        m = r._mapping
        entry = float(m["entry_price"]) if m["entry_price"] is not None else 0.0
        tp = (
            float(m["take_profit_price"])
            if m["take_profit_price"] is not None
            else None
        )
        cur = float(m["current_price"]) if m["current_price"] is not None else None
        qty = float(m["quantity"]) if m["quantity"] is not None else 0.0

        pnl_usdt: Optional[float] = None
        pnl_pct: Optional[float] = None
        if cur is not None and entry > 0:
            pnl_usdt = round((cur - entry) * qty, 8)
            pnl_pct = round((cur - entry) / entry * 100.0, 4)

        margin: Optional[float] = None
        if cur is not None and tp is not None and tp != entry:
            raw = (cur - entry) / (tp - entry) * 100.0
            # Clamp 0..100: negative = below entry (would render
            # confusingly as ``-12%`` toward TP); >100 = TP overshoot
            # (the trade should have been closed by the sell engine).
            margin = round(max(0.0, min(100.0, raw)), 2)

        if cur is None:
            label = "aguardando"
        elif pnl_usdt is not None and pnl_usdt < 0:
            label = "underwater"
        else:
            label = "holding"

        items.append({
            "trade_id": str(m["id"]),
            "symbol": m["symbol"],
            "entry_price": entry,
            "current_price": cur,
            "quantity": qty,
            "invested_value": (
                float(m["invested_value"])
                if m["invested_value"] is not None
                else None
            ),
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "tp_price": tp,
            "margin_to_target_pct": margin,
            "status": m["status"],
            "status_label": label,
        })

    return {
        "items": items,
        "count": len(items),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
