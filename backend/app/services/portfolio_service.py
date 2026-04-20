"""Portfolio service — live Gate-backed portfolio snapshots with DB fallbacks."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..exchange_adapters.gate_adapter import GateAdapter
from ..models.exchange_connection import ExchangeConnection
from ..models.trade import Trade
from ..utils.encryption import decrypt
from .analytics_service import analytics_service

logger = logging.getLogger(__name__)

_STABLE_CURRENCIES = {"USDT", "USD", "USDC"}
_OPEN_STATUSES = ("open", "ACTIVE", "HOLDING_UNDERWATER")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        logger.debug("Unable to convert value to float: %r", value)
        return default


def _normalize_dt(value: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        if len(value) <= 10 and end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class PortfolioService:
    async def _get_gate_adapter(self, db: AsyncSession, user_id: UUID) -> Optional[GateAdapter]:
        result = await db.execute(
            select(ExchangeConnection)
            .where(
                ExchangeConnection.user_id == user_id,
                ExchangeConnection.exchange_name == "gate.io",
                ExchangeConnection.is_active,
            )
            .order_by(ExchangeConnection.execution_priority.asc(), ExchangeConnection.created_at.asc())
            .limit(1)
        )
        conn = result.scalars().first()
        if not conn:
            return None

        raw_key = bytes(conn.api_key_encrypted) if isinstance(conn.api_key_encrypted, memoryview) else conn.api_key_encrypted
        raw_secret = bytes(conn.api_secret_encrypted) if isinstance(conn.api_secret_encrypted, memoryview) else conn.api_secret_encrypted
        api_key = decrypt(raw_key).strip()
        api_secret = decrypt(raw_secret).strip()
        if not api_key or not api_secret:
            return None
        return GateAdapter(api_key, api_secret)

    async def _get_open_trade_map(self, db: AsyncSession, user_id: UUID) -> Dict[str, List[Trade]]:
        result = await db.execute(
            select(Trade)
            .where(
                Trade.user_id == user_id,
                Trade.status.in_(_OPEN_STATUSES),
            )
            .order_by(Trade.entry_at.desc())
        )
        grouped: Dict[str, List[Trade]] = defaultdict(list)
        for trade in result.scalars().all():
            grouped[f"{trade.market_type}:{trade.symbol.upper()}"].append(trade)
        return grouped

    def _summarize_trade_cluster(self, trades: Iterable[Trade]) -> Dict[str, float]:
        trade_list = list(trades)
        quantity = sum(_safe_float(t.quantity) for t in trade_list)
        invested = sum(_safe_float(t.invested_value) for t in trade_list)
        if quantity > 0:
            weighted_entry = sum(_safe_float(t.entry_price) * _safe_float(t.quantity) for t in trade_list) / quantity
        else:
            weighted_entry = 0.0
        return {
            "quantity": quantity,
            "invested_value": invested,
            "entry_price": weighted_entry,
        }

    async def _build_live_positions(
        self,
        db: AsyncSession,
        user_id: UUID,
        min_value_usdt: float,
    ) -> Dict[str, Any]:
        open_trade_map = await self._get_open_trade_map(db, user_id)
        adapter = await self._get_gate_adapter(db, user_id)
        if not adapter:
            return await self._build_db_positions(open_trade_map, min_value_usdt)

        try:
            spot_balances, spot_tickers, futures_balance, futures_positions = await asyncio.gather(
                adapter.get_spot_balance(),
                adapter.get_tickers(market="spot"),
                adapter.get_futures_balance(),
                adapter.list_futures_positions(),
            )
        except Exception as exc:
            logger.warning("Falling back to DB positions after Gate fetch failure: %s", exc)
            return await self._build_db_positions(open_trade_map, min_value_usdt)

        spot_prices = {
            str(t.get("currency_pair", "")).upper(): _safe_float(t.get("last"))
            for t in spot_tickers
            if t.get("currency_pair")
        }

        positions: List[Dict[str, Any]] = []
        spot_total_value = 0.0

        for balance in spot_balances:
            currency = str(balance.get("currency", "")).upper()
            qty = _safe_float(balance.get("available")) + _safe_float(balance.get("locked"))
            if qty <= 0:
                continue

            if currency in _STABLE_CURRENCIES:
                price = 1.0
            else:
                price = spot_prices.get(f"{currency}_USDT", 0.0)
            value_usdt = qty * price
            spot_total_value += value_usdt

            if currency in _STABLE_CURRENCIES or value_usdt < min_value_usdt:
                continue

            db_key = f"spot:{currency}_USDT"
            trade_summary = self._summarize_trade_cluster(open_trade_map.get(db_key, []))
            invested_value = trade_summary["invested_value"] or value_usdt
            entry_price = trade_summary["entry_price"] or price
            profit_loss = value_usdt - invested_value
            profit_loss_pct = (profit_loss / invested_value * 100.0) if invested_value > 0 else 0.0

            positions.append({
                "id": f"spot:{currency}",
                "source": "exchange",
                "market_type": "spot",
                "symbol": f"{currency}_USDT",
                "asset": currency,
                "direction": "long",
                "entry_price": round(entry_price, 8),
                "mark_price": round(price, 8),
                "current_price": round(price, 8),
                "quantity": round(qty, 8),
                "invested_value": round(invested_value, 2),
                "current_value": round(value_usdt, 2),
                "profit_loss": round(profit_loss, 2),
                "profit_loss_pct": round(profit_loss_pct, 2),
                "status": "open",
                "close_supported": False,
            })

        futures_equity = _safe_float(futures_balance.get("equity")) or _safe_float(futures_balance.get("total"))
        if futures_equity <= 0:
            futures_equity = _safe_float(futures_balance.get("available")) + _safe_float(futures_balance.get("unrealised_pnl"))

        futures_total_unrealized = 0.0
        futures_open_value = 0.0

        for position in futures_positions:
            size = _safe_float(position.get("size"))
            value_usdt = abs(_safe_float(position.get("value")))
            if size == 0 or value_usdt < min_value_usdt:
                continue

            contract = str(position.get("contract", "")).upper()
            entry_price = _safe_float(position.get("entry_price"))
            mark_price = _safe_float(position.get("mark_price"))
            unrealized_pnl = _safe_float(position.get("unrealised_pnl"))
            futures_total_unrealized += unrealized_pnl
            futures_open_value += value_usdt
            profit_loss_pct = (unrealized_pnl / value_usdt * 100.0) if value_usdt > 0 else 0.0

            positions.append({
                "id": f"futures:{contract}",
                "source": "exchange",
                "market_type": "futures",
                "symbol": contract,
                "asset": contract.replace("_USDT", ""),
                "direction": "long" if size > 0 else "short",
                "entry_price": round(entry_price, 8),
                "mark_price": round(mark_price, 8),
                "current_price": round(mark_price, 8),
                "quantity": abs(size),
                "invested_value": round(value_usdt, 2),
                "current_value": round(value_usdt + unrealized_pnl, 2),
                "profit_loss": round(unrealized_pnl, 2),
                "profit_loss_pct": round(profit_loss_pct, 2),
                "status": "open",
                "leverage": _safe_float(position.get("leverage")),
                "liq_price": _safe_float(position.get("liq_price")),
                "close_supported": False,
            })

        positions.sort(key=lambda item: item.get("current_value", 0.0), reverse=True)
        portfolio_total_value = spot_total_value + futures_equity

        return {
            "source": "exchange",
            "positions": positions,
            "totals": {
                "spot_value": round(spot_total_value, 2),
                "futures_value": round(futures_equity, 2),
                "portfolio_value": round(portfolio_total_value, 2),
                "unrealized_pnl": round(sum(_safe_float(p.get("profit_loss")) for p in positions), 2),
                "futures_unrealized_pnl": round(futures_total_unrealized, 2),
                "futures_open_value": round(futures_open_value, 2),
            },
        }

    async def _build_db_positions(self, open_trade_map: Dict[str, List[Trade]], min_value_usdt: float) -> Dict[str, Any]:
        positions: List[Dict[str, Any]] = []
        for trades in open_trade_map.values():
            sample = trades[0]
            summary = self._summarize_trade_cluster(trades)
            current_value = summary["invested_value"]
            if current_value < min_value_usdt:
                continue
            positions.append({
                "id": str(sample.id),
                "source": "database",
                "market_type": sample.market_type,
                "symbol": sample.symbol,
                "asset": sample.symbol.replace("_USDT", ""),
                "direction": sample.direction,
                "entry_price": round(summary["entry_price"], 8),
                "mark_price": round(summary["entry_price"], 8),
                "current_price": round(summary["entry_price"], 8),
                "quantity": round(summary["quantity"], 8),
                "invested_value": round(summary["invested_value"], 2),
                "current_value": round(current_value, 2),
                "profit_loss": 0.0,
                "profit_loss_pct": 0.0,
                "status": sample.status,
                "close_supported": True,
            })

        total_value = sum(_safe_float(item.get("current_value")) for item in positions)
        positions.sort(key=lambda item: item.get("current_value", 0.0), reverse=True)
        return {
            "source": "database",
            "positions": positions,
            "totals": {
                "spot_value": round(sum(_safe_float(p.get("current_value")) for p in positions if p.get("market_type") == "spot"), 2),
                "futures_value": round(sum(_safe_float(p.get("current_value")) for p in positions if p.get("market_type") == "futures"), 2),
                "portfolio_value": round(total_value, 2),
                "unrealized_pnl": 0.0,
                "futures_unrealized_pnl": 0.0,
                "futures_open_value": round(sum(_safe_float(p.get("current_value")) for p in positions if p.get("market_type") == "futures"), 2),
            },
        }

    async def get_live_positions(
        self,
        db: AsyncSession,
        user_id: UUID,
        min_value_usdt: float = 10.0,
    ) -> Dict[str, Any]:
        payload = await self._build_live_positions(db, user_id, min_value_usdt)
        payload["count"] = len(payload["positions"])
        return payload

    async def get_dashboard_overview(
        self,
        db: AsyncSession,
        user_id: UUID,
        days: int = 30,
        min_value_usdt: float = 10.0,
    ) -> Dict[str, Any]:
        live = await self._build_live_positions(db, user_id, min_value_usdt)
        today_summary = await analytics_service.get_daily_summary(db, user_id)
        total_summary = await analytics_service.get_pnl_summary(db, user_id)

        start = datetime.now(timezone.utc) - timedelta(days=days)
        range_summary = await analytics_service.get_pnl_summary(db, user_id, start_date=start)
        current_capital = _safe_float(live["totals"].get("portfolio_value"))
        initial_capital = max(current_capital - _safe_float(range_summary.get("total_pnl")), 0.0)
        capital_evolution = await analytics_service.get_capital_evolution(
            db,
            user_id,
            days=days,
            initial_capital=initial_capital,
        )

        return {
            "today_pnl": round(_safe_float(today_summary.get("total_pnl")), 2),
            "consolidated_pnl": round(
                _safe_float(total_summary.get("total_pnl")) + _safe_float(live["totals"].get("unrealized_pnl")),
                2,
            ),
            "realized_total_pnl": round(_safe_float(total_summary.get("total_pnl")), 2),
            "unrealized_pnl": round(_safe_float(live["totals"].get("unrealized_pnl")), 2),
            "win_rate": round(_safe_float(today_summary.get("win_rate")), 2),
            "open_positions_count": len(live["positions"]),
            "open_positions": live["positions"],
            "portfolio_value": round(current_capital, 2),
            "spot_value": round(_safe_float(live["totals"].get("spot_value")), 2),
            "futures_value": round(_safe_float(live["totals"].get("futures_value")), 2),
            "capital_evolution": capital_evolution,
            "source": live["source"],
        }

    def parse_history_window(
        self,
        period_days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        if period_days:
            end = datetime.now(timezone.utc)
            return end - timedelta(days=period_days), end
        return _normalize_dt(start_date), _normalize_dt(end_date, end_of_day=True)


portfolio_service = PortfolioService()
