"""Execution Engine — executes trades via exchange adapters and manages order lifecycle."""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.trade import Trade
from ..models.order import Order
from ..models.exchange_connection import ExchangeConnection
from ..utils.encryption import decrypt

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Executes trades using the user's exchange API keys via adapters."""

    async def execute_trade(
        self,
        db: AsyncSession,
        user_id: UUID,
        pool_id: Optional[UUID],
        symbol: str,
        direction: str,
        market_type: str,
        risk_params: Dict[str, Any],
        indicators: Dict[str, Any],
        alpha_score: float,
        exchange_name: str = "gate.io",
        paper_mode: bool = True,
    ) -> Dict[str, Any]:
        """Execute a trade order.

        In paper mode: records the trade without sending to exchange.
        In live mode: sends order to exchange then records.
        """
        quantity = risk_params.get("quantity", 0)
        current_price = indicators.get("close", 0)

        if quantity <= 0 or current_price <= 0:
            return {"success": False, "error": "Invalid quantity or price"}

        side = "buy" if direction == "long" else "sell"
        order_type = risk_params.get("order_type", "limit")
        exchange_order_id = None

        if not paper_mode:
            # Get user's exchange connection
            result = await db.execute(
                select(ExchangeConnection).where(
                    ExchangeConnection.user_id == user_id,
                    ExchangeConnection.is_active == True,
                )
            )
            conn = result.scalars().first()
            if not conn:
                return {"success": False, "error": "No active exchange connection"}

            try:
                raw_key = conn.api_key_encrypted
                raw_secret = conn.api_secret_encrypted
                if isinstance(raw_key, memoryview):
                    raw_key = bytes(raw_key)
                if isinstance(raw_secret, memoryview):
                    raw_secret = bytes(raw_secret)

                api_key = decrypt(raw_key)
                api_secret = decrypt(raw_secret)

                # Use appropriate exchange adapter
                exchange_order_id = await self._send_order_to_exchange(
                    exchange=conn.exchange_name,
                    api_key=api_key,
                    api_secret=api_secret,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    price=current_price,
                )
            except Exception as e:
                logger.exception(f"Failed to execute order on exchange: {e}")
                return {"success": False, "error": f"Exchange error: {str(e)}"}
        else:
            exchange_order_id = f"PAPER-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

        # Record trade in database
        try:
            trade = Trade(
                user_id=user_id,
                pool_id=pool_id,
                symbol=symbol,
                side=side,
                direction=direction,
                market_type=market_type,
                exchange=exchange_name,
                entry_price=current_price,
                quantity=quantity,
                invested_value=risk_params.get("invested_value", quantity * current_price),
                status="open",
                alpha_score_at_entry=alpha_score,
                indicators_at_entry=indicators,
                take_profit_price=risk_params.get("take_profit_price"),
                stop_loss_price=risk_params.get("stop_loss_price"),
                entry_at=datetime.now(timezone.utc),
            )
            db.add(trade)

            # Record order
            order = Order(
                trade_id=trade.id,
                user_id=user_id,
                exchange_order_id=exchange_order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                price=current_price,
                quantity=quantity,
                filled_quantity=quantity if paper_mode else None,
                status="filled" if paper_mode else "pending",
                exchange=exchange_name,
            )
            db.add(order)
            await db.commit()
            await db.refresh(trade)

            logger.info(
                f"{'PAPER' if paper_mode else 'LIVE'} trade executed: "
                f"{side} {quantity} {symbol} @ {current_price} | "
                f"TP={risk_params.get('take_profit_price')} SL={risk_params.get('stop_loss_price')}"
            )

            return {
                "success": True,
                "trade_id": str(trade.id),
                "order_id": exchange_order_id,
                "paper_mode": paper_mode,
            }

        except Exception as e:
            await db.rollback()
            logger.exception(f"Failed to record trade: {e}")
            return {"success": False, "error": f"DB error: {str(e)}"}

    async def close_trade(
        self,
        db: AsyncSession,
        trade_id: UUID,
        exit_price: float,
        exit_reason: str = "manual",
        paper_mode: bool = True,
    ) -> Dict[str, Any]:
        """Close an open trade."""
        result = await db.execute(select(Trade).where(Trade.id == trade_id, Trade.status == "open"))
        trade = result.scalars().first()
        if not trade:
            return {"success": False, "error": "Trade not found or already closed"}

        entry_price = float(trade.entry_price)
        quantity = float(trade.quantity)

        if trade.direction == "long":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity

        pnl_pct = (pnl / float(trade.invested_value)) * 100 if trade.invested_value else 0
        now = datetime.now(timezone.utc)
        holding_seconds = int((now - trade.entry_at).total_seconds()) if trade.entry_at else 0

        trade.exit_price = exit_price
        trade.profit_loss = round(pnl, 2)
        trade.profit_loss_pct = round(pnl_pct, 4)
        trade.status = "closed"
        trade.exit_at = now
        trade.holding_seconds = holding_seconds

        await db.commit()

        logger.info(f"Trade {trade_id} closed: P&L={pnl:.2f} ({pnl_pct:.2f}%) | Reason: {exit_reason}")

        return {
            "success": True,
            "trade_id": str(trade_id),
            "profit_loss": round(pnl, 2),
            "profit_loss_pct": round(pnl_pct, 4),
            "holding_seconds": holding_seconds,
            "exit_reason": exit_reason,
        }

    async def _send_order_to_exchange(
        self, exchange: str, api_key: str, api_secret: str,
        symbol: str, side: str, order_type: str, quantity: float, price: float,
    ) -> str:
        """Send order to exchange. Returns exchange order ID."""
        import httpx
        import time
        import hashlib
        import hmac

        if exchange.lower() in ("gate.io", "gateio"):
            host = "api.gateio.ws"
            prefix = "/api/v4"

            if order_type == "market":
                endpoint = "/spot/orders"
                body_dict = {
                    "currency_pair": symbol.replace("USDT", "_USDT"),
                    "side": side,
                    "type": "market",
                    "amount": str(quantity),
                }
            else:
                endpoint = "/spot/orders"
                body_dict = {
                    "currency_pair": symbol.replace("USDT", "_USDT"),
                    "side": side,
                    "type": "limit",
                    "amount": str(quantity),
                    "price": str(price),
                    "time_in_force": "gtc",
                }

            import json
            body = json.dumps(body_dict)
            t = str(int(time.time()))
            hashed_body = hashlib.sha512(body.encode("utf-8")).hexdigest()
            sign_string = f"POST\n{prefix}{endpoint}\n\n{hashed_body}\n{t}"
            sign = hmac.new(api_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha512).hexdigest()

            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "KEY": api_key,
                "Timestamp": t,
                "SIGN": sign,
            }

            async with httpx.AsyncClient() as client:
                r = await client.post(f"https://{host}{prefix}{endpoint}", headers=headers, content=body)
                if r.status_code in (200, 201):
                    data = r.json()
                    return data.get("id", f"GATE-{t}")
                else:
                    raise Exception(f"Gate.io order failed: {r.status_code} {r.text}")

        raise Exception(f"Exchange adapter not implemented for: {exchange}")


execution_engine = ExecutionEngine()
