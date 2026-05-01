"""Trade Sync Service — imports closed spot orders from Gate.io into the trades table."""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..exchange_adapters.gate_adapter import GateAdapter
from ..models.exchange_connection import ExchangeConnection
from ..models.trade import Trade
from ..utils.encryption import decrypt

logger = logging.getLogger(__name__)


class TradeSyncService:

    # ── credentials ───────────────────────────────────────────────────────────

    async def _get_gate_adapter(
        self, db: AsyncSession, user_id: UUID
    ) -> Optional[GateAdapter]:
        result = await db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.user_id == user_id,
                ExchangeConnection.is_active == True,
            )
        )
        conn = result.scalars().first()
        if not conn:
            return None
        raw_key = (
            bytes(conn.api_key_encrypted)
            if isinstance(conn.api_key_encrypted, memoryview)
            else conn.api_key_encrypted
        )
        raw_secret = (
            bytes(conn.api_secret_encrypted)
            if isinstance(conn.api_secret_encrypted, memoryview)
            else conn.api_secret_encrypted
        )
        api_key = decrypt(raw_key).strip()
        api_secret = decrypt(raw_secret).strip()
        if not api_key or not api_secret:
            return None
        return GateAdapter(api_key, api_secret)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_ts(ts_value: Any) -> Optional[datetime]:
        if not ts_value:
            return None
        try:
            return datetime.fromtimestamp(float(ts_value), tz=timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_symbol(currency_pair: str) -> str:
        return currency_pair.replace("/", "_").upper()

    # ── already-imported set ──────────────────────────────────────────────────

    async def _get_existing_order_ids(
        self, db: AsyncSession, user_id: UUID
    ) -> set:
        result = await db.execute(
            select(Trade.exchange_order_id).where(
                Trade.user_id == user_id,
                Trade.exchange_order_id.isnot(None),
            )
        )
        return {row[0] for row in result.fetchall()}

    # ── fetch all pages ───────────────────────────────────────────────────────

    async def _fetch_all_closed_orders(
        self, adapter: GateAdapter, days: int, all_history: bool = False
    ) -> List[Dict[str, Any]]:
        from datetime import datetime, timezone
        from_ts: Optional[int] = None
        if all_history:
            # Gate.io launched in 2017; use 2017-01-01 as the earliest safe timestamp
            from_ts = int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp())

        all_orders: List[Dict[str, Any]] = []
        page = 1
        while True:
            try:
                batch = await adapter.get_my_closed_spot_orders(
                    days=days, page=page, limit=100, from_timestamp=from_ts
                )
            except Exception as exc:
                logger.warning(f"Error fetching closed orders page {page}: {exc}")
                break
            if not batch:
                break
            all_orders.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return all_orders

    # ── FIFO matching ─────────────────────────────────────────────────────────

    def _match_orders(
        self, orders: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        FIFO-match buy and sell orders per currency pair.

        Returns:
          matched_trades  — list of dicts describing closed round-trips
          open_positions  — list of unmatched buy orders still holding
        """
        by_pair: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for o in orders:
            if o.get("status") != "closed":
                continue
            filled = self._safe_float(o.get("filled_total", 0))
            if filled <= 0:
                continue
            by_pair[o["currency_pair"]].append(o)

        matched_trades: List[Dict[str, Any]] = []
        open_positions: List[Dict[str, Any]] = []

        for pair, pair_orders in by_pair.items():
            pair_orders.sort(key=lambda x: self._safe_float(x.get("create_time", 0)))

            buy_queue: List[Dict[str, Any]] = []
            for order in pair_orders:
                if order["side"] == "buy":
                    buy_queue.append({
                        "order": order,
                        "remaining_qty": self._safe_float(order.get("amount", 0)),
                    })
                elif order["side"] == "sell" and buy_queue:
                    sell_qty = self._safe_float(order.get("amount", 0))
                    sell_price = self._safe_float(
                        order.get("avg_deal_price") or order.get("price", 0)
                    )
                    sell_fee = self._safe_float(order.get("fee", 0))
                    sell_ts = self._parse_ts(order.get("finish_time") or order.get("create_time"))
                    sell_order_id = str(order.get("id", ""))

                    while sell_qty > 0 and buy_queue:
                        head = buy_queue[0]
                        take_qty = min(sell_qty, head["remaining_qty"])
                        buy_order = head["order"]
                        buy_price = self._safe_float(
                            buy_order.get("avg_deal_price") or buy_order.get("price", 0)
                        )
                        buy_fee_total = self._safe_float(buy_order.get("fee", 0))
                        buy_total_qty = self._safe_float(buy_order.get("amount", 0))
                        buy_fee = (
                            buy_fee_total * (take_qty / buy_total_qty)
                            if buy_total_qty
                            else 0.0
                        )
                        fee_usdt = buy_fee + sell_fee * (take_qty / sell_qty if sell_qty else 1)

                        invested = round(take_qty * buy_price, 4)
                        exit_value = round(take_qty * sell_price, 4)
                        pnl = round(exit_value - invested - fee_usdt, 4)
                        pnl_pct = round((pnl / invested * 100) if invested else 0, 4)

                        holding_secs: Optional[int] = None
                        buy_ts = self._parse_ts(
                            buy_order.get("finish_time") or buy_order.get("create_time")
                        )
                        if buy_ts and sell_ts:
                            holding_secs = int((sell_ts - buy_ts).total_seconds())

                        matched_trades.append({
                            "symbol": self._normalize_symbol(pair),
                            "side": "buy",
                            "direction": "long",
                            "market_type": "spot",
                            "exchange": "gate",
                            "entry_price": buy_price,
                            "exit_price": sell_price,
                            "quantity": take_qty,
                            "invested_value": invested,
                            "profit_loss": pnl,
                            "profit_loss_pct": pnl_pct,
                            "fee": round(fee_usdt, 8),
                            "status": "closed",
                            "entry_at": self._parse_ts(
                                buy_order.get("finish_time") or buy_order.get("create_time")
                            ),
                            "exit_at": sell_ts,
                            "holding_seconds": holding_secs,
                            "exchange_order_id": sell_order_id,
                            "source": "exchange_import",
                        })

                        head["remaining_qty"] -= take_qty
                        sell_qty -= take_qty
                        if head["remaining_qty"] <= 1e-8:
                            buy_queue.pop(0)

            for item in buy_queue:
                if item["remaining_qty"] > 1e-8:
                    open_positions.append(item["order"])

        return matched_trades, open_positions

    # ── public method ─────────────────────────────────────────────────────────

    async def sync_spot_trades(
        self,
        db: AsyncSession,
        user_id: UUID,
        days: int = 90,
        all_history: bool = False,
    ) -> Dict[str, Any]:
        """
        Pull closed spot orders from Gate.io, match buy/sell pairs (FIFO),
        and insert new Trade records into the database.

        When all_history=True, fetches from Gate.io's launch (2017-01-01)
        regardless of the days parameter.

        Returns a summary dict with counts.
        """
        adapter = await self._get_gate_adapter(db, user_id)
        if not adapter:
            return {
                "success": False,
                "error": "No active exchange connection found. Please connect your Gate.io account first.",
            }

        try:
            raw_orders = await self._fetch_all_closed_orders(adapter, days, all_history=all_history)
        except Exception as exc:
            logger.error(f"Failed to fetch exchange orders: {exc}")
            return {"success": False, "error": f"Exchange API error: {exc}"}

        if not raw_orders:
            return {
                "success": True,
                "imported": 0,
                "skipped": 0,
                "open_positions_found": 0,
                "message": "No closed orders found in the selected period.",
            }

        matched, open_pos = self._match_orders(raw_orders)

        existing_ids = await self._get_existing_order_ids(db, user_id)

        imported = 0
        skipped = 0
        for trade_data in matched:
            oid = trade_data.get("exchange_order_id")
            if oid and oid in existing_ids:
                skipped += 1
                continue

            trade = Trade(
                user_id=user_id,
                **{k: v for k, v in trade_data.items()},
            )
            db.add(trade)
            if oid:
                existing_ids.add(oid)
            imported += 1

        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.error("DB commit failed during trade sync: %s", exc, exc_info=True)
            return {"success": False, "error": f"Database error: {exc}"}

        return {
            "success": True,
            "imported": imported,
            "skipped": skipped,
            "open_positions_found": len(open_pos),
            "message": (
                f"Imported {imported} trade(s). "
                f"Skipped {skipped} already-imported. "
                f"{len(open_pos)} open position(s) not imported (still holding)."
            ),
        }


trade_sync_service = TradeSyncService()
