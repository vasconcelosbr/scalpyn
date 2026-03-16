from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from uuid import UUID

from ..database import get_db
from ..models.trade import Trade
from .config import get_current_user_id

router = APIRouter(prefix="/api/trades", tags=["Trades"])


@router.get("/positions")
async def get_open_positions(
    market_type: Optional[str] = Query(None, description="Filter by market type: spot, futures, tradfi"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Return open positions where invested_value > 1 USD."""
    query = select(Trade).where(
        Trade.user_id == user_id,
        Trade.status == "open",
        Trade.invested_value > 1,
    )
    if market_type:
        query = query.where(Trade.market_type == market_type.lower())
    query = query.order_by(Trade.entry_at.desc())

    result = await db.execute(query)
    positions = result.scalars().all()

    return {
        "positions": [
            {
                "id": str(p.id),
                "symbol": p.symbol,
                "side": p.side,
                "market_type": p.market_type,
                "exchange": p.exchange,
                "entry_price": float(p.entry_price),
                "quantity": float(p.quantity),
                "invested_value": float(p.invested_value),
                "take_profit_price": float(p.take_profit_price) if p.take_profit_price else None,
                "stop_loss_price": float(p.stop_loss_price) if p.stop_loss_price else None,
                "alpha_score_at_entry": float(p.alpha_score_at_entry) if p.alpha_score_at_entry else None,
                "entry_at": p.entry_at.isoformat() if p.entry_at else None,
            }
            for p in positions
        ]
    }


@router.get("/")
async def get_trade_history(
    market_type: Optional[str] = Query(None, description="Filter by market type: spot, futures, tradfi"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Return closed trade history."""
    query = select(Trade).where(
        Trade.user_id == user_id,
        Trade.status == "closed",
    )
    if market_type:
        query = query.where(Trade.market_type == market_type.lower())
    query = query.order_by(Trade.exit_at.desc())

    result = await db.execute(query)
    trades = result.scalars().all()

    return {
        "trades": [
            {
                "id": str(t.id),
                "symbol": t.symbol,
                "side": t.side,
                "market_type": t.market_type,
                "exchange": t.exchange,
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price) if t.exit_price else None,
                "quantity": float(t.quantity),
                "invested_value": float(t.invested_value),
                "profit_loss": float(t.profit_loss) if t.profit_loss is not None else None,
                "profit_loss_pct": float(t.profit_loss_pct) if t.profit_loss_pct is not None else None,
                "holding_seconds": t.holding_seconds,
                "alpha_score_at_entry": float(t.alpha_score_at_entry) if t.alpha_score_at_entry else None,
                "entry_at": t.entry_at.isoformat() if t.entry_at else None,
                "exit_at": t.exit_at.isoformat() if t.exit_at else None,
            }
            for t in trades
        ]
    }
