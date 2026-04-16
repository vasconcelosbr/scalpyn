"""Gate.io WebSocket event handlers.

Each handler receives the ``result`` list from a Gate.io WS update message and
performs DB updates, alerting, and broadcast to connected frontend clients.

All handlers are fully fire-and-forget: they catch every exception internally
and never propagate errors back to the dispatch loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from ..database import AsyncSessionLocal
from ..models.trade import Trade
from ..api.websocket import broadcast_trade_event

if TYPE_CHECKING:
    from .gate_ws_client import GateWSClient

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

_SCALPYN_TAG = "t-scalpyn"

# Distance from liquidation price below which we fire an anti-liq alert (8 %)
_LIQ_ALERT_THRESHOLD = Decimal("0.08")

# Minimum relative change in unrealised_pnl that triggers a broadcast (1 %)
_PNL_BROADCAST_THRESHOLD = Decimal("0.01")


def _to_decimal(value: Any, fallback: Decimal = Decimal("0")) -> Decimal:
    """Safely convert *value* to Decimal, returning *fallback* on failure."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return fallback


def _is_our_order(text: str | None) -> bool:
    return bool(text and _SCALPYN_TAG in text)


# ── Handler: futures.positions ────────────────────────────────────────────────

async def handle_futures_positions(result: list[dict]) -> None:
    """Handle Gate.io futures position updates.

    For each position in *result*:
    - Locate the matching ACTIVE/open Trade in the DB by symbol.
    - Update ``hwm_price`` (high-water-mark) when mark_price moves favourably.
    - Broadcast a position_update event when unrealised_pnl changes >1 %.
    - Broadcast a liq_alert event when the mark price is within 8 % of liq_price.
    """
    try:
        async with AsyncSessionLocal() as session:
            for pos in result:
                try:
                    await _process_futures_position(session, pos)
                except Exception as exc:
                    logger.warning(
                        "handle_futures_positions: error processing position %s: %s",
                        pos.get("contract"), exc, exc_info=True,
                    )
            await session.commit()
    except Exception as exc:
        logger.warning("handle_futures_positions: session error: %s", exc, exc_info=True)


async def _process_futures_position(session, pos: dict) -> None:
    contract: str = pos.get("contract", "")
    if not contract:
        return

    mark_price = _to_decimal(pos.get("mark_price"))
    entry_price = _to_decimal(pos.get("entry_price"))
    liq_price = _to_decimal(pos.get("liq_price"))
    unrealised_pnl = _to_decimal(pos.get("unrealised_pnl"))
    size = _to_decimal(pos.get("size"))

    # A size of 0 means the position is flat — nothing to track
    if size == 0:
        return

    stmt = (
        select(Trade)
        .where(
            Trade.symbol == contract,
            Trade.market_type == "futures",
            Trade.status.in_(["ACTIVE", "open"]),
        )
        .limit(1)
    )
    row = await session.execute(stmt)
    trade: Trade | None = row.scalar_one_or_none()

    if trade is None:
        logger.debug("handle_futures_positions: no active futures trade found for %s", contract)
        return

    direction: str = (trade.direction or "long").lower()
    updates: dict[str, Any] = {}

    # ── High-water-mark tracking ──────────────────────────────────────────────
    current_hwm = _to_decimal(trade.hwm_price) if trade.hwm_price is not None else None

    if direction == "long" and mark_price > entry_price:
        if current_hwm is None or mark_price > current_hwm:
            updates["hwm_price"] = mark_price
            current_hwm = mark_price
    elif direction == "short" and mark_price < entry_price:
        if current_hwm is None or mark_price < current_hwm:
            updates["hwm_price"] = mark_price
            current_hwm = mark_price

    # ── Liq price change → update funding cost proxy ──────────────────────────
    trade_liq = _to_decimal(trade.liq_price) if trade.liq_price is not None else Decimal("0")
    if liq_price > 0 and liq_price != trade_liq:
        updates["liq_price"] = liq_price
        # funding_cost_usdt is stored separately; update if provided
        funding_fee = _to_decimal(pos.get("funding_fee_income"))
        if funding_fee != 0:
            new_funding = _to_decimal(trade.funding_cost_usdt) + funding_fee
            updates["funding_cost_usdt"] = new_funding

    # ── Apply DB updates ──────────────────────────────────────────────────────
    if updates:
        await session.execute(
            update(Trade).where(Trade.id == trade.id).values(**updates)
        )

    # ── Broadcast position_update if PnL changed significantly ───────────────
    old_pnl = _to_decimal(
        (trade.engine_meta or {}).get("last_unrealised_pnl") if trade.engine_meta else None
    )
    pnl_ref = abs(old_pnl) if old_pnl != 0 else Decimal("1")
    pnl_change = abs(unrealised_pnl - old_pnl) / pnl_ref

    if pnl_change >= _PNL_BROADCAST_THRESHOLD:
        await broadcast_trade_event("position_update", {
            "trade_id": str(trade.id),
            "symbol": contract,
            "direction": direction,
            "mark_price": float(mark_price),
            "entry_price": float(entry_price),
            "unrealised_pnl": float(unrealised_pnl),
            "liq_price": float(liq_price),
            "hwm_price": float(current_hwm) if current_hwm else None,
        })

        # Persist last known PnL in engine_meta to detect future changes
        meta = dict(trade.engine_meta or {})
        meta["last_unrealised_pnl"] = str(unrealised_pnl)
        await session.execute(
            update(Trade).where(Trade.id == trade.id).values(engine_meta=meta)
        )

    # ── Anti-liq distance alert ───────────────────────────────────────────────
    if liq_price > 0 and mark_price > 0:
        if direction == "long":
            distance = (mark_price - liq_price) / mark_price
        else:
            distance = (liq_price - mark_price) / mark_price

        if distance < _LIQ_ALERT_THRESHOLD:
            logger.warning(
                "Anti-liq alert: %s distance to liq=%.2f%% (mark=%s liq=%s)",
                contract, float(distance) * 100, mark_price, liq_price,
            )
            await broadcast_trade_event("liq_alert", {
                "trade_id": str(trade.id),
                "symbol": contract,
                "direction": direction,
                "mark_price": float(mark_price),
                "liq_price": float(liq_price),
                "distance_pct": round(float(distance) * 100, 2),
            })


# ── Handler: futures.orders ───────────────────────────────────────────────────

async def handle_futures_orders(result: list[dict]) -> None:
    """Handle Gate.io futures order fill/cancel events.

    Identifies orders tagged with 't-scalpyn', determines their role
    (TP1/TP2/SL), updates the DB, and broadcasts the appropriate event.
    """
    try:
        async with AsyncSessionLocal() as session:
            for order in result:
                try:
                    await _process_futures_order(session, order)
                except Exception as exc:
                    logger.warning(
                        "handle_futures_orders: error processing order %s: %s",
                        order.get("id"), exc, exc_info=True,
                    )
            await session.commit()
    except Exception as exc:
        logger.warning("handle_futures_orders: session error: %s", exc, exc_info=True)


async def _process_futures_order(session, order: dict) -> None:
    order_id = str(order.get("id", ""))
    contract: str = order.get("contract", "")
    status: str = order.get("status", "")
    text: str = order.get("text", "") or ""
    fill_price = _to_decimal(order.get("fill_price") or order.get("price"))
    size = _to_decimal(order.get("size"))

    logger.info(
        "futures.orders: id=%s contract=%s status=%s text=%s fill_price=%s",
        order_id, contract, status, text, fill_price,
    )

    if status != "finished" or fill_price <= 0:
        return

    if not _is_our_order(text):
        logger.debug("futures.orders: order %s not ours (text=%r) — skipping", order_id, text)
        return

    # Determine order role
    text_lower = text.lower()
    role: str
    if "tp1" in text_lower:
        role = "tp1"
    elif "tp2" in text_lower:
        role = "tp2"
    elif "sl" in text_lower:
        role = "sl"
    else:
        role = "unknown"

    # Find matching trade
    stmt = (
        select(Trade)
        .where(
            Trade.symbol == contract,
            Trade.market_type == "futures",
            Trade.status.in_(["ACTIVE", "open"]),
        )
        .limit(1)
    )
    row = await session.execute(stmt)
    trade: Trade | None = row.scalar_one_or_none()

    if trade is None:
        logger.warning(
            "futures.orders: filled order %s (role=%s) but no active trade found for %s",
            order_id, role, contract,
        )
        await broadcast_trade_event("order_filled", {
            "order_id": order_id,
            "symbol": contract,
            "role": role,
            "fill_price": float(fill_price),
            "trade_id": None,
        })
        return

    updates: dict[str, Any] = {}

    if role == "tp1":
        updates["tp1_hit"] = True
        logger.info("TP1 hit: trade=%s symbol=%s fill=%.6f", trade.id, contract, fill_price)

    elif role == "tp2":
        updates["tp2_hit"] = True
        logger.info("TP2 hit: trade=%s symbol=%s fill=%.6f", trade.id, contract, fill_price)

    elif role == "sl":
        updates["status"] = "CLOSED"
        updates["exit_price"] = fill_price
        updates["exit_at"] = datetime.now(timezone.utc)
        logger.info("SL hit — closing trade: trade=%s symbol=%s fill=%.6f", trade.id, contract, fill_price)

    if updates:
        await session.execute(
            update(Trade).where(Trade.id == trade.id).values(**updates)
        )

    await broadcast_trade_event("order_filled", {
        "trade_id": str(trade.id),
        "order_id": order_id,
        "symbol": contract,
        "role": role,
        "fill_price": float(fill_price),
        "status": updates.get("status", trade.status),
    })


# ── Handler: futures.autoorders ───────────────────────────────────────────────

async def handle_futures_autoorders(result: list[dict]) -> None:
    """Handle Gate.io price-trigger (auto-order) events.

    When a trigger fires (status == 'finish'), broadcast a trigger_fired event.
    This signals that a TP or SL price trigger was activated on the exchange.
    """
    try:
        for trigger in result:
            try:
                await _process_futures_autoorder(trigger)
            except Exception as exc:
                logger.warning(
                    "handle_futures_autoorders: error processing trigger %s: %s",
                    trigger.get("id"), exc, exc_info=True,
                )
    except Exception as exc:
        logger.warning("handle_futures_autoorders: unexpected error: %s", exc, exc_info=True)


async def _process_futures_autoorder(trigger: dict) -> None:
    trigger_id = str(trigger.get("id", ""))
    status: str = trigger.get("status", "")
    contract: str = trigger.get("initial", {}).get("contract", "") if isinstance(trigger.get("initial"), dict) else ""
    trigger_price = _to_decimal(
        (trigger.get("trigger") or {}).get("price") if isinstance(trigger.get("trigger"), dict) else trigger.get("trigger_price")
    )
    order_id = str(trigger.get("order_id", "") or "")

    logger.info(
        "futures.autoorders: id=%s contract=%s status=%s trigger_price=%s order_id=%s",
        trigger_id, contract, status, trigger_price, order_id,
    )

    if status != "finish":
        return

    logger.info(
        "Price trigger FIRED: id=%s contract=%s price=%s order_id=%s",
        trigger_id, contract, trigger_price, order_id,
    )

    await broadcast_trade_event("trigger_fired", {
        "trigger_id": trigger_id,
        "symbol": contract,
        "trigger_price": float(trigger_price),
        "order_id": order_id,
    })


# ── Handler: futures.liquidates ───────────────────────────────────────────────

async def handle_futures_liquidates(result: list[dict]) -> None:
    """Handle Gate.io liquidation events.

    CRITICAL: a liquidation means Scalpyn's anti-liq layers have failed.
    - Marks the matching trade as CLOSED with reason='liquidated'.
    - Logs at CRITICAL level.
    - Broadcasts an emergency_alert event to all connected clients.
    """
    try:
        async with AsyncSessionLocal() as session:
            for liq in result:
                try:
                    await _process_futures_liquidate(session, liq)
                except Exception as exc:
                    logger.warning(
                        "handle_futures_liquidates: error processing liquidation %s: %s",
                        liq.get("contract"), exc, exc_info=True,
                    )
            await session.commit()
    except Exception as exc:
        logger.warning("handle_futures_liquidates: session error: %s", exc, exc_info=True)


async def _process_futures_liquidate(session, liq: dict) -> None:
    contract: str = liq.get("contract", "")
    fill_price = _to_decimal(liq.get("fill_price") or liq.get("order_price"))
    order_price = _to_decimal(liq.get("order_price"))
    size = _to_decimal(liq.get("size"))
    liq_time = liq.get("time")

    logger.critical(
        "LIQUIDATION DETECTED: contract=%s fill_price=%s size=%s time=%s",
        contract, fill_price, size, liq_time,
    )

    # Find and close the matching trade
    stmt = (
        select(Trade)
        .where(
            Trade.symbol == contract,
            Trade.market_type == "futures",
            Trade.status.in_(["ACTIVE", "open"]),
        )
        .limit(1)
    )
    row = await session.execute(stmt)
    trade: Trade | None = row.scalar_one_or_none()

    trade_id: str | None = None
    if trade is not None:
        trade_id = str(trade.id)
        meta = dict(trade.engine_meta or {})
        meta["close_reason"] = "liquidated"
        await session.execute(
            update(Trade)
            .where(Trade.id == trade.id)
            .values(
                status="CLOSED",
                exit_price=fill_price if fill_price > 0 else order_price,
                exit_at=datetime.now(timezone.utc),
                engine_meta=meta,
            )
        )
        logger.critical(
            "Trade %s marked CLOSED (liquidated): symbol=%s fill=%.6f",
            trade.id, contract, fill_price,
        )
    else:
        logger.critical(
            "Liquidation for %s but no matching active trade found in DB",
            contract,
        )

    await broadcast_trade_event("emergency_alert", {
        "type": "LIQUIDATED",
        "symbol": contract,
        "fill_price": float(fill_price),
        "order_price": float(order_price),
        "size": float(size),
        "trade_id": trade_id,
        "liq_time": liq_time,
    })


# ── Handler: spot.orders ──────────────────────────────────────────────────────

async def handle_spot_orders(result: list[dict]) -> None:
    """Handle Gate.io spot order fill/update events.

    - Identifies orders tagged with 't-scalpyn'.
    - On closed sell → marks trade CLOSED.
    - On closed buy → confirms trade ACTIVE.
    - Broadcasts appropriate events to connected clients.
    """
    try:
        async with AsyncSessionLocal() as session:
            for order in result:
                try:
                    await _process_spot_order(session, order)
                except Exception as exc:
                    logger.warning(
                        "handle_spot_orders: error processing order %s: %s",
                        order.get("id"), exc, exc_info=True,
                    )
            await session.commit()
    except Exception as exc:
        logger.warning("handle_spot_orders: session error: %s", exc, exc_info=True)


async def _process_spot_order(session, order: dict) -> None:
    order_id = str(order.get("id", ""))
    currency_pair: str = order.get("currency_pair", "")
    status: str = order.get("status", "")
    side: str = (order.get("side") or "").lower()
    text: str = order.get("text", "") or ""
    filled_amount = _to_decimal(order.get("filled_amount") or order.get("amount"))
    avg_deal_price = _to_decimal(order.get("avg_deal_price") or order.get("price"))

    logger.info(
        "spot.orders: id=%s pair=%s status=%s side=%s text=%s avg_price=%s",
        order_id, currency_pair, status, side, text, avg_deal_price,
    )

    if status != "closed":
        return

    if not _is_our_order(text):
        logger.debug("spot.orders: order %s not ours (text=%r) — skipping", order_id, text)
        return

    # Find the matching spot trade
    stmt = (
        select(Trade)
        .where(
            Trade.symbol == currency_pair,
            Trade.market_type == "spot",
        )
    )

    # For sell (close): look for ACTIVE/open trade
    if side == "sell":
        stmt = stmt.where(Trade.status.in_(["ACTIVE", "open"]))
    else:
        # Buy confirmation: look for pending/open trade
        stmt = stmt.where(Trade.status.in_(["PENDING", "open"]))

    stmt = stmt.limit(1)
    row = await session.execute(stmt)
    trade: Trade | None = row.scalar_one_or_none()

    if trade is None:
        logger.warning(
            "spot.orders: order %s (%s %s) has no matching spot trade in DB",
            order_id, side, currency_pair,
        )
        await broadcast_trade_event("spot_order_filled", {
            "order_id": order_id,
            "symbol": currency_pair,
            "side": side,
            "fill_price": float(avg_deal_price),
            "filled_amount": float(filled_amount),
            "trade_id": None,
        })
        return

    updates: dict[str, Any] = {}

    if side == "sell":
        # Calculate P&L
        entry_price = _to_decimal(trade.entry_price)
        quantity = _to_decimal(trade.quantity)
        gross_pnl = (avg_deal_price - entry_price) * quantity
        gross_pnl_pct = (
            (avg_deal_price - entry_price) / entry_price * 100
            if entry_price > 0 else Decimal("0")
        )

        updates = {
            "status": "CLOSED",
            "exit_price": avg_deal_price,
            "exit_at": datetime.now(timezone.utc),
            "profit_loss": gross_pnl,
            "profit_loss_pct": gross_pnl_pct,
        }

        logger.info(
            "Spot trade CLOSED: trade=%s symbol=%s pnl=%.4f (%.2f%%)",
            trade.id, currency_pair, gross_pnl, gross_pnl_pct,
        )

        await session.execute(
            update(Trade).where(Trade.id == trade.id).values(**updates)
        )

        await broadcast_trade_event("spot_order_filled", {
            "trade_id": str(trade.id),
            "order_id": order_id,
            "symbol": currency_pair,
            "side": side,
            "fill_price": float(avg_deal_price),
            "filled_amount": float(filled_amount),
            "status": "CLOSED",
            "profit_loss": float(gross_pnl),
            "profit_loss_pct": float(gross_pnl_pct),
        })

    elif side == "buy":
        # Confirm the trade as ACTIVE with actual fill price
        updates = {
            "status": "ACTIVE",
            "entry_price": avg_deal_price,
        }

        await session.execute(
            update(Trade).where(Trade.id == trade.id).values(**updates)
        )

        logger.info(
            "Spot trade ACTIVE confirmed: trade=%s symbol=%s fill=%.6f qty=%s",
            trade.id, currency_pair, avg_deal_price, filled_amount,
        )

        await broadcast_trade_event("spot_order_filled", {
            "trade_id": str(trade.id),
            "order_id": order_id,
            "symbol": currency_pair,
            "side": side,
            "fill_price": float(avg_deal_price),
            "filled_amount": float(filled_amount),
            "status": "ACTIVE",
        })


# ── Registration ──────────────────────────────────────────────────────────────

def register_all_handlers(ws_client: "GateWSClient") -> None:
    """Register all event handlers with the WS client."""
    ws_client.register_handler("futures.positions", handle_futures_positions)
    ws_client.register_handler("futures.orders", handle_futures_orders)
    ws_client.register_handler("futures.autoorders", handle_futures_autoorders)
    ws_client.register_handler("futures.liquidates", handle_futures_liquidates)
    ws_client.register_handler("spot.orders", handle_spot_orders)

    logger.info("All Gate.io WS event handlers registered")
