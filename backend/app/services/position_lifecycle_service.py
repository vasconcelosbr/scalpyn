"""Position Lifecycle Service — FIFO matching engine over ``exchange_executions``.

Task #257.

Algorithm
---------
For each ``(user_id, exchange, symbol, market_type)`` we maintain a FIFO queue
of OPEN lots (entry fills not yet matched by a closing fill). We replay all
``exchange_executions`` rows in chronological order:

* Spot:   ``side='buy'`` opens a LONG lot; ``side='sell'`` consumes lots.
* Futures: A futures position direction inversion is detected per-symbol — the
  first fill defines the direction; subsequent fills with the same side add
  to the position; opposite side fills close lots. (Gate futures normally
  uses ``size>0`` for long fills and ``size<0`` for short fills — see
  ``executions_sync_service._normalize_futures``.)

Each closing fill produces ONE ``position_lifecycle`` row per lot it touches.
Partial closes therefore generate multiple lifecycle rows. Fees are rateably
attributed by ``qty`` proportion. PnL is realised:

  * long  : (avg_exit - avg_entry) * qty - fees
  * short : (avg_entry - avg_exit) * qty - fees

The full table is rebuilt on every run (idempotent: TRUNCATE + INSERT inside
a single transaction). For the dataset sizes we expect (≤ 50k fills/user)
this is dramatically simpler than incremental updates and bug-free.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.position_lifecycle import PositionLifecycle

logger = logging.getLogger(__name__)


def _f(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fee_in_quote(fee: Any, fee_currency: Any, symbol: str, price: float) -> float:
    """Return a Gate fee in the symbol's quote currency.

    Spot buy fees are commonly charged in the purchased base asset. Gate's
    payload reports that asset quantity, so it must be multiplied by the fill
    price before it can participate in USDT P&L.
    """
    amount = _f(fee)
    currency = str(fee_currency or "").upper()
    parts = symbol.upper().replace("-", "_").split("_")
    base = parts[0] if parts else ""
    quote = parts[-1] if len(parts) > 1 else ""
    if not amount or not currency or currency == quote:
        return amount
    if currency == base:
        return amount * price
    return 0.0


class _Lot:
    __slots__ = ("qty_open", "qty_total", "price", "fee_total", "trade_id",
                 "order_id", "ts", "role")

    def __init__(self, qty: float, price: float, fee: float, trade_id: str,
                 order_id: Optional[str], ts: datetime, role: Optional[str]):
        self.qty_open = qty
        self.qty_total = qty
        self.price = price
        self.fee_total = fee
        self.trade_id = trade_id
        self.order_id = order_id
        self.ts = ts
        self.role = role


class PositionLifecycleService:

    async def rebuild_for_user(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> Dict[str, Any]:
        """Replay every execution for the user and rewrite ``position_lifecycle``."""
        rows = (await db.execute(text(
            """
            SELECT id, market_type, symbol, side, role, price, quantity, fee,
                   fee_currency, trade_id, order_id, executed_at
              FROM exchange_executions
             WHERE user_id = :uid
             ORDER BY executed_at ASC, id ASC
            """
        ), {"uid": str(user_id)})).all()

        # group by (symbol, market_type)
        groups: Dict[Tuple[str, str], List[Any]] = defaultdict(list)
        for r in rows:
            groups[(r.symbol, r.market_type)].append(r)

        lifecycle_rows: List[Dict[str, Any]] = []
        open_positions: List[Dict[str, Any]] = []

        for (symbol, market_type), fills in groups.items():
            closed_rows, open_lots = self._process_group(
                user_id=user_id,
                symbol=symbol,
                market_type=market_type,
                fills=fills,
            )
            lifecycle_rows.extend(closed_rows)
            open_positions.extend(open_lots)

        # Wipe and reinsert atomically — `position_lifecycle` is a derived
        # projection of `exchange_executions`, so total rebuild is the
        # simplest and most correct strategy at our data sizes.
        try:
            await db.execute(text(
                "DELETE FROM position_lifecycle WHERE user_id = :uid"
            ), {"uid": str(user_id)})

            for row in lifecycle_rows + open_positions:
                db.add(PositionLifecycle(**row))

            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.exception("position_lifecycle rebuild failed: %s", exc)
            return {"success": False, "error": str(exc)}

        return {
            "success": True,
            "fills_processed": len(rows),
            "lifecycle_rows_closed": len(lifecycle_rows),
            "open_positions": len(open_positions),
        }

    # ── internals ───────────────────────────────────────────────────────────
    def _process_group(
        self,
        user_id: UUID,
        symbol: str,
        market_type: str,
        fills: List[Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Replay one (symbol, market_type) stream of fills via FIFO."""
        lots: deque[_Lot] = deque()
        # Direction of the currently OPEN position. None means flat.
        # In spot we treat side='buy' as long-open and side='sell' as long-close
        # (no shorts in spot). In futures we honour direction inversions: when
        # the lot stack is empty, a fill OPENS the side it indicates.
        direction: Optional[str] = None
        closed_rows: List[Dict[str, Any]] = []

        for fill in fills:
            qty = _f(fill.quantity)
            if qty <= 0:
                continue
            price = _f(fill.price)
            fee = _fee_in_quote(fill.fee, fill.fee_currency, symbol, price)
            ts = fill.executed_at if isinstance(fill.executed_at, datetime) else None
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            side = (fill.side or "").lower()
            trade_id = str(fill.trade_id)
            order_id = str(fill.order_id) if fill.order_id else None
            role = (fill.role or "").lower() or None

            if market_type == "spot":
                if side == "buy":
                    lots.append(_Lot(qty, price, fee, trade_id, order_id, ts, role))
                    direction = "long"
                else:  # sell
                    if not lots:
                        # Sell without prior buy in window — emit a DRIFT
                        # row so the orphan close is visible and auditable
                        # instead of being silently dropped (likely a
                        # deposit, transfer-in, or pre-window inventory).
                        closed_rows.append(self._drift_row(
                            user_id=user_id, symbol=symbol, market_type=market_type,
                            direction="long", qty=qty, price=price,
                            ts=ts, close_trade_id=trade_id,
                        ))
                        continue
                    rows, remainder = self._close_against_lots(
                        user_id=user_id, symbol=symbol, market_type=market_type,
                        direction="long", lots=lots,
                        close_qty=qty, close_price=price, close_fee=fee,
                        close_trade_id=trade_id, close_order_id=order_id,
                        close_ts=ts, close_role=role,
                    )
                    closed_rows.extend(rows)
                    if remainder > 1e-9:
                        # Spot can't go short — emit DRIFT row for the orphan
                        # close (entry leg likely older than backfill window).
                        closed_rows.append(self._drift_row(
                            user_id=user_id, symbol=symbol, market_type=market_type,
                            direction="long", qty=remainder, price=price,
                            ts=ts, close_trade_id=trade_id,
                        ))
                    if not lots:
                        direction = None
            else:
                # futures — supports position flip
                fill_dir = "long" if side == "buy" else "short"
                if not lots:
                    direction = fill_dir
                    lots.append(_Lot(qty, price, fee, trade_id, order_id, ts, role))
                elif direction == fill_dir:
                    lots.append(_Lot(qty, price, fee, trade_id, order_id, ts, role))
                else:
                    # opposite side closes against current lots; if it
                    # exceeds the open exposure, the remainder OPENS a new
                    # position in the reverse direction (futures position
                    # flip — a normal trader workflow on Gate).
                    rows, remainder = self._close_against_lots(
                        user_id=user_id, symbol=symbol, market_type=market_type,
                        direction=direction or fill_dir, lots=lots,
                        close_qty=qty, close_price=price, close_fee=fee,
                        close_trade_id=trade_id, close_order_id=order_id,
                        close_ts=ts, close_role=role,
                    )
                    closed_rows.extend(rows)
                    if remainder > 1e-9:
                        # Pro-rate the close fill's fee for the reversal lot
                        # so we don't double-count.
                        reverse_fee = fee * (remainder / qty) if qty > 0 else 0.0
                        lots.clear()
                        direction = fill_dir
                        lots.append(_Lot(remainder, price, reverse_fee,
                                          trade_id, order_id, ts, role))
                    elif not lots:
                        direction = None

        # remaining lots are still-open positions
        open_rows: List[Dict[str, Any]] = []
        if lots and direction:
            qty_total = sum(l.qty_open for l in lots)
            if qty_total > 0:
                avg_entry = sum(l.qty_open * l.price for l in lots) / qty_total
                fees = sum(l.fee_total * (l.qty_open / l.qty_total)
                           for l in lots if l.qty_total > 0)
                opened_at = lots[0].ts
                open_rows.append({
                    "user_id": user_id,
                    "exchange": "gate",
                    "symbol": symbol,
                    "market_type": market_type,
                    "direction": direction,
                    "opened_at": opened_at,
                    "closed_at": None,
                    "holding_seconds": None,
                    "qty": Decimal(repr(qty_total)),
                    "avg_entry": Decimal(repr(avg_entry)),
                    "avg_exit": None,
                    "invested_usdt": Decimal(repr(qty_total * avg_entry)),
                    "final_usdt": None,
                    "fees_total": Decimal(repr(fees)),
                    "pnl_usdt": None,
                    "pnl_pct": None,
                    "roi": None,
                    "status": "open",
                    "n_fills_in": len(lots),
                    "n_fills_out": 0,
                    "entry_trade_ids": [l.trade_id for l in lots],
                    "exit_trade_ids": [],
                    "slippage_estimate": None,
                    "maker_taker_ratio": None,
                    "data_quality": "OK",
                })

        return closed_rows, open_rows

    def _close_against_lots(
        self,
        user_id: UUID,
        symbol: str,
        market_type: str,
        direction: str,
        lots: deque,
        close_qty: float,
        close_price: float,
        close_fee: float,
        close_trade_id: str,
        close_order_id: Optional[str],
        close_ts: datetime,
        close_role: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], float]:
        """Consume lots FIFO. Returns (closed_rows, leftover_qty).

        ``leftover_qty`` > 0 means the closing fill exceeded the open
        exposure. The caller decides what to do with the remainder
        (spot → emit DRIFT; futures → open reverse position).
        """
        produced: List[Dict[str, Any]] = []
        remaining = close_qty
        # Snapshot the close-fill total qty for fee proration.
        total_close_qty = close_qty if close_qty > 0 else 1.0
        n_lots_consumed = 0

        while remaining > 1e-12 and lots:
            head = lots[0]
            take = min(remaining, head.qty_open)
            entry_price = head.price
            entry_ts = head.ts
            entry_trade_id = head.trade_id
            # Rateable fees:
            entry_fee_share = (
                head.fee_total * (take / head.qty_total) if head.qty_total > 0 else 0.0
            )
            exit_fee_share = close_fee * (take / total_close_qty)
            fees = entry_fee_share + exit_fee_share

            invested = take * entry_price
            final = take * close_price
            if direction == "long":
                pnl = (close_price - entry_price) * take - fees
            else:
                pnl = (entry_price - close_price) * take - fees
            pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
            roi = (pnl / invested) if invested > 0 else 0.0
            holding = int((close_ts - entry_ts).total_seconds())

            roles = [r for r in (head.role, close_role) if r]
            maker_count = sum(1 for r in roles if r == "maker")
            mt_ratio = (maker_count / len(roles)) if roles else None

            produced.append({
                "user_id": user_id,
                "exchange": "gate",
                "symbol": symbol,
                "market_type": market_type,
                "direction": direction,
                "opened_at": entry_ts,
                "closed_at": close_ts,
                "holding_seconds": max(0, holding),
                "qty": Decimal(repr(take)),
                "avg_entry": Decimal(repr(entry_price)),
                "avg_exit": Decimal(repr(close_price)),
                "invested_usdt": Decimal(repr(invested)),
                "final_usdt": Decimal(repr(final)),
                "fees_total": Decimal(repr(fees)),
                "pnl_usdt": Decimal(repr(pnl)),
                "pnl_pct": Decimal(repr(pnl_pct)),
                "roi": Decimal(repr(roi)),
                "status": "closed",
                "n_fills_in": 1,
                "n_fills_out": 1,
                "entry_trade_ids": [entry_trade_id],
                "exit_trade_ids": [close_trade_id],
                "slippage_estimate": None,
                "maker_taker_ratio": Decimal(repr(mt_ratio)) if mt_ratio is not None else None,
                "data_quality": "OK",
            })

            head.qty_open -= take
            remaining -= take
            n_lots_consumed += 1
            if head.qty_open <= 1e-12:
                lots.popleft()

        return produced, max(0.0, remaining)

    def _drift_row(
        self,
        *,
        user_id: UUID,
        symbol: str,
        market_type: str,
        direction: str,
        qty: float,
        price: float,
        ts: datetime,
        close_trade_id: str,
    ) -> Dict[str, Any]:
        """Synthetic row when a closing fill has no matching open lot."""
        return {
            "user_id": user_id,
            "exchange": "gate",
            "symbol": symbol,
            "market_type": market_type,
            "direction": direction,
            "opened_at": ts,
            "closed_at": ts,
            "holding_seconds": 0,
            "qty": Decimal(repr(qty)),
            "avg_entry": Decimal(repr(price)),
            "avg_exit": Decimal(repr(price)),
            "invested_usdt": Decimal(repr(qty * price)),
            "final_usdt": Decimal(repr(qty * price)),
            "fees_total": Decimal("0"),
            "pnl_usdt": Decimal("0"),
            "pnl_pct": Decimal("0"),
            "roi": Decimal("0"),
            "status": "closed",
            "n_fills_in": 0,
            "n_fills_out": 1,
            "entry_trade_ids": [],
            "exit_trade_ids": [close_trade_id],
            "slippage_estimate": None,
            "maker_taker_ratio": None,
            "data_quality": "DRIFT",
        }


position_lifecycle_service = PositionLifecycleService()
