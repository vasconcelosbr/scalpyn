"""
Celery Task — anti-liquidation monitor for open futures positions.
Runs every 30 seconds.

For each open futures position that carries a ``liq_price``:
  1. Fetch the current price from the ``market_metadata`` table (no API call).
  2. Compute ``distance_pct`` = distance from current price to liquidation price
     as a percentage of current price.
  3. Emergency zone (distance_pct <= 3.0 %):
       - Load the user's Gate.io adapter.
       - Call ``close_position(symbol)`` via the adapter.
       - Mark the trade CLOSED in the DB with ``exit_reason="anti_liq_emergency"``.
       - Broadcast an EMERGENCY alert via the WebSocket layer.
  4. Alert zone (3.0 < distance_pct <= 8.0 %):
       - Broadcast an ANTI_LIQ_WARNING alert.
       - Log a warning.
  5. Catch all per-position exceptions so one bad position never aborts the loop.

Returns the count of positions checked.

Registered as: ``app.tasks.anti_liq_monitor.monitor``
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# Distance thresholds (percentage from current price to liquidation price).
_EMERGENCY_PCT: float = 3.0
_ALERT_PCT: float = 8.0


# ── Async runner ──────────────────────────────────────────────────────────────

def _run_async(coro) -> Any:
    """Execute an async coroutine in a fresh event loop (Celery worker context)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Core async logic ──────────────────────────────────────────────────────────

async def _monitor_async() -> int:
    from sqlalchemy import text, select

    from ..database import AsyncSessionLocal
    from ..models.trade import Trade
    from ..models.exchange_connection import ExchangeConnection
    from ..exchange_adapters.gate_adapter import GateAdapter
    from ..utils.encryption import decrypt
    from ..websocket.scalpyn_ws_server import broadcast_alert

    logger.info("Anti-liq monitor: starting scan of open futures positions")
    checked = 0

    async with AsyncSessionLocal() as db:
        # ── 1. Load all open futures positions that have a liq_price ─────────
        result = await db.execute(
            select(Trade).where(
                Trade.market_type == "futures",
                Trade.status.in_(["ACTIVE", "open"]),
            )
        )
        trades: list[Trade] = result.scalars().all()

        futures_trades = [t for t in trades if t.take_profit_price is not None or True]
        # Filter in Python to only those with a stored liq_price in indicators.
        # liq_price is stored in indicators_at_entry JSONB under key "liq_price".
        futures_trades = [
            t for t in trades
            if t.indicators_at_entry and t.indicators_at_entry.get("liq_price")
        ]

        if not futures_trades:
            logger.info("Anti-liq monitor: no futures positions with liq_price found")
            return 0

        logger.info("Anti-liq monitor: checking %d positions", len(futures_trades))

        for trade in futures_trades:
            try:
                checked += 1
                liq_price: float = float(trade.indicators_at_entry["liq_price"])
                symbol: str = trade.symbol
                user_id: str = str(trade.user_id)
                direction: str = (trade.direction or "long").lower()

                # ── 2. Fetch current price from market_metadata (no API call) ─
                price_row = await db.execute(
                    text("SELECT price FROM market_metadata WHERE symbol = :sym"),
                    {"sym": symbol},
                )
                price_row = price_row.fetchone()
                if not price_row:
                    logger.debug(
                        "Anti-liq monitor: no market_metadata price for %s — skipping",
                        symbol,
                    )
                    continue

                current_price: float = float(price_row.price)
                if current_price <= 0 or liq_price <= 0:
                    continue

                # ── 3. Compute distance to liquidation ────────────────────────
                if direction == "long":
                    distance_pct = (
                        (current_price - liq_price) / current_price * 100
                        if current_price > liq_price
                        else 0.0
                    )
                else:  # short
                    distance_pct = (
                        (liq_price - current_price) / current_price * 100
                        if liq_price > current_price
                        else 0.0
                    )

                alert_details: dict[str, Any] = {
                    "trade_id": str(trade.id),
                    "symbol": symbol,
                    "direction": direction,
                    "current_price": current_price,
                    "liq_price": liq_price,
                    "distance_pct": round(distance_pct, 2),
                    "entry_price": float(trade.entry_price),
                }

                # ── 4a. EMERGENCY: force close immediately ────────────────────
                if distance_pct <= _EMERGENCY_PCT:
                    logger.critical(
                        "ANTI-LIQ EMERGENCY: %s distance_pct=%.2f%% — force closing",
                        symbol,
                        distance_pct,
                    )
                    await _execute_emergency_close(
                        db=db,
                        trade=trade,
                        current_price=current_price,
                        alert_details=alert_details,
                        broadcast_alert=broadcast_alert,
                    )

                # ── 4b. ALERT ZONE: warn ──────────────────────────────────────
                elif distance_pct <= _ALERT_PCT:
                    logger.warning(
                        "ANTI-LIQ WARNING: %s distance_pct=%.2f%% (threshold=%.1f%%)",
                        symbol,
                        distance_pct,
                        _ALERT_PCT,
                    )
                    await broadcast_alert(user_id, "ANTI_LIQ_WARNING", alert_details)

            except Exception as exc:
                logger.exception(
                    "Anti-liq monitor: unhandled error on trade %s: %s",
                    getattr(trade, "id", "?"),
                    exc,
                )

    logger.info("Anti-liq monitor: scan complete — %d positions checked", checked)
    return checked


async def _execute_emergency_close(
    *,
    db,
    trade,
    current_price: float,
    alert_details: dict[str, Any],
    broadcast_alert,
) -> None:
    """
    Attempt to close the position via the exchange adapter, then mark the
    trade as CLOSED in the DB regardless of whether the API call succeeded
    (to prevent the monitor from repeatedly attempting on stale data).
    """
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from ..models.exchange_connection import ExchangeConnection
    from ..exchange_adapters.gate_adapter import GateAdapter
    from ..utils.encryption import decrypt

    user_id: str = str(trade.user_id)
    symbol: str = trade.symbol

    # ── Load the user's active exchange connection ────────────────────────────
    conn_result = await db.execute(
        select(ExchangeConnection).where(
            ExchangeConnection.user_id == trade.user_id,
            ExchangeConnection.is_active == True,  # noqa: E712
        ).order_by(ExchangeConnection.execution_priority).limit(1)
    )
    conn = conn_result.scalars().first()

    if conn:
        try:
            raw_key = conn.api_key_encrypted
            raw_secret = conn.api_secret_encrypted
            adapter = GateAdapter(
                decrypt(raw_key).strip(),
                decrypt(raw_secret).strip(),
            )
            await adapter.close_position(symbol)
            logger.info(
                "Anti-liq EMERGENCY: position %s closed via exchange adapter",
                symbol,
            )
        except Exception as exc:
            logger.error(
                "Anti-liq EMERGENCY: adapter.close_position(%s) failed: %s — "
                "marking trade closed anyway to prevent re-attempts",
                symbol,
                exc,
            )
    else:
        logger.error(
            "Anti-liq EMERGENCY: no active exchange connection for user %s — "
            "cannot close %s via API; marking CLOSED in DB",
            user_id,
            symbol,
        )

    # ── Mark trade CLOSED in DB ───────────────────────────────────────────────
    try:
        now = datetime.now(timezone.utc)
        entry_price = float(trade.entry_price)
        qty = float(trade.quantity)
        direction = (trade.direction or "long").lower()

        if direction == "long":
            raw_pnl = (current_price - entry_price) * qty
        else:
            raw_pnl = (entry_price - current_price) * qty

        pnl_pct = (
            (current_price - entry_price) / entry_price * 100
            if direction == "long"
            else (entry_price - current_price) / entry_price * 100
        )

        trade.status = "CLOSED"
        trade.exit_price = current_price
        trade.exit_at = now
        trade.profit_loss = round(raw_pnl, 2)
        trade.profit_loss_pct = round(pnl_pct, 4)
        trade.holding_seconds = int((now - trade.entry_at).total_seconds()) if trade.entry_at else None

        # Persist the exit reason in indicators_at_entry to avoid a schema change.
        indicators = dict(trade.indicators_at_entry or {})
        indicators["exit_reason"] = "anti_liq_emergency"
        trade.indicators_at_entry = indicators

        await db.commit()
        logger.info("Anti-liq EMERGENCY: trade %s marked CLOSED in DB", trade.id)
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception(
            "Anti-liq EMERGENCY: DB update failed for trade %s: %s",
            trade.id,
            exc,
        )

    # ── Broadcast emergency alert ─────────────────────────────────────────────
    await broadcast_alert(
        user_id,
        "EMERGENCY",
        {
            **alert_details,
            "action": "FORCE_CLOSED",
            "exit_price": current_price,
        },
    )


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.anti_liq_monitor.monitor", bind=True, max_retries=0)
def monitor(self) -> str:
    """
    Celery periodic task — anti-liquidation monitor.
    Scheduled every 30 seconds via beat_schedule in celery_app.py.
    """
    count = _run_async(_monitor_async())
    return f"Anti-liq monitor: {count} positions checked"
