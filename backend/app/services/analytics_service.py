"""Analytics Service — P&L calculations, performance metrics, capital evolution."""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from uuid import UUID

from sqlalchemy import select, func, case, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.trade import Trade

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Calculates trading performance metrics from trade history."""

    async def get_pnl_summary(
        self, db: AsyncSession, user_id: UUID,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get P&L summary for a date range."""
        query = select(Trade).where(
            Trade.user_id == user_id,
            Trade.status == "closed",
        )
        if start_date:
            query = query.where(Trade.exit_at >= start_date)
        if end_date:
            query = query.where(Trade.exit_at <= end_date)

        result = await db.execute(query)
        trades = result.scalars().all()

        if not trades:
            return self._empty_summary()

        total_pnl = sum(float(t.profit_loss or 0) for t in trades)
        total_invested = sum(float(t.invested_value or 0) for t in trades)
        wins = [t for t in trades if t.profit_loss and float(t.profit_loss) > 0]
        losses = [t for t in trades if t.profit_loss and float(t.profit_loss) <= 0]

        win_rate = (len(wins) / len(trades) * 100) if trades else 0
        avg_profit = (sum(float(t.profit_loss) for t in wins) / len(wins)) if wins else 0
        avg_loss = (sum(float(t.profit_loss) for t in losses) / len(losses)) if losses else 0
        profit_factor = (abs(sum(float(t.profit_loss) for t in wins)) / abs(sum(float(t.profit_loss) for t in losses))) if losses and sum(float(t.profit_loss) for t in losses) != 0 else float("inf")

        # Sharpe Ratio (simplified, annualized)
        returns = [float(t.profit_loss_pct or 0) for t in trades]
        sharpe = self._calc_sharpe(returns)

        # Max Drawdown
        max_dd = self._calc_max_drawdown(trades)

        # Avg holding time
        holding_times = [t.holding_seconds for t in trades if t.holding_seconds]
        avg_holding = (sum(holding_times) / len(holding_times)) if holding_times else 0

        return {
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round((total_pnl / total_invested * 100) if total_invested else 0, 2),
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(win_rate, 2),
            "avg_profit": round(avg_profit, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": max_dd,
            "avg_holding_seconds": int(avg_holding),
            "best_trade": round(max((float(t.profit_loss) for t in trades), default=0), 2),
            "worst_trade": round(min((float(t.profit_loss) for t in trades), default=0), 2),
        }

    async def get_capital_evolution(
        self, db: AsyncSession, user_id: UUID, days: int = 30, initial_capital: float = 100000,
    ) -> List[Dict[str, Any]]:
        """Get cumulative P&L over time for chart."""
        start = datetime.now(timezone.utc) - timedelta(days=days)
        query = (
            select(Trade)
            .where(Trade.user_id == user_id, Trade.status == "closed", Trade.exit_at >= start)
            .order_by(Trade.exit_at.asc())
        )
        result = await db.execute(query)
        trades = result.scalars().all()

        cumulative = initial_capital
        data_points = [{"time": start.isoformat(), "value": initial_capital}]

        for trade in trades:
            cumulative += float(trade.profit_loss or 0)
            data_points.append({
                "time": trade.exit_at.isoformat() if trade.exit_at else "",
                "value": round(cumulative, 2),
                "symbol": trade.symbol,
                "pnl": float(trade.profit_loss or 0),
            })

        return data_points

    async def get_daily_summary(
        self, db: AsyncSession, user_id: UUID,
    ) -> Dict[str, Any]:
        """Get today's trading summary for dashboard and notifications."""
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        # Today's closed trades
        query = select(Trade).where(
            Trade.user_id == user_id,
            Trade.status == "closed",
            Trade.exit_at >= today_start,
        )
        result = await db.execute(query)
        closed_today = result.scalars().all()

        # Open positions
        query_open = select(Trade).where(
            Trade.user_id == user_id, Trade.status == "open"
        )
        result_open = await db.execute(query_open)
        open_trades = result_open.scalars().all()

        today_pnl = sum(float(t.profit_loss or 0) for t in closed_today)
        wins = len([t for t in closed_today if t.profit_loss and float(t.profit_loss) > 0])
        total = len(closed_today)

        # Consecutive losses (for circuit breaker)
        recent_query = (
            select(Trade)
            .where(Trade.user_id == user_id, Trade.status == "closed")
            .order_by(Trade.exit_at.desc())
            .limit(10)
        )
        recent_result = await db.execute(recent_query)
        recent = recent_result.scalars().all()
        consecutive_losses = 0
        for t in recent:
            if t.profit_loss and float(t.profit_loss) < 0:
                consecutive_losses += 1
            else:
                break

        return {
            "total_pnl": round(today_pnl, 2),
            "trades_count": total,
            "win_rate": round((wins / total * 100) if total else 0, 1),
            "open_positions": len(open_trades),
            "consecutive_losses": consecutive_losses,
            "open_positions_data": [
                {
                    "id": str(t.id),
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry_price": float(t.entry_price),
                    "quantity": float(t.quantity),
                    "invested_value": float(t.invested_value),
                    "take_profit_price": float(t.take_profit_price) if t.take_profit_price else None,
                    "stop_loss_price": float(t.stop_loss_price) if t.stop_loss_price else None,
                }
                for t in open_trades
            ],
        }

    def _calc_sharpe(self, returns: List[float], risk_free_rate: float = 0.0) -> Optional[float]:
        if len(returns) < 2:
            return None
        import numpy as np
        arr = np.array(returns)
        mean_ret = arr.mean() - risk_free_rate
        std_ret = arr.std()
        if std_ret == 0:
            return None
        sharpe = (mean_ret / std_ret) * (365 ** 0.5)  # Annualized
        return round(float(sharpe), 2)

    def _calc_max_drawdown(self, trades: list) -> float:
        if not trades:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in sorted(trades, key=lambda x: x.exit_at or datetime.min.replace(tzinfo=timezone.utc)):
            cumulative += float(t.profit_loss or 0)
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown
        return round(max_dd, 2)

    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "total_pnl": 0, "total_pnl_pct": 0, "total_trades": 0,
            "winning_trades": 0, "losing_trades": 0, "win_rate": 0,
            "avg_profit": 0, "avg_loss": 0, "profit_factor": None,
            "sharpe_ratio": None, "max_drawdown_pct": 0,
            "avg_holding_seconds": 0, "best_trade": 0, "worst_trade": 0,
        }


analytics_service = AnalyticsService()
