"""Trades API — trade history, open positions, close trades."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional
from uuid import UUID
from datetime import datetime

from ..database import get_db
from ..models.trade import Trade
from .config import get_current_user_id

router = APIRouter(prefix="/api/trades", tags=["Trades"])


@router.get("/")
async def get_trades(
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    market_type: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    query = select(Trade).where(Trade.user_id == user_id).order_by(desc(Trade.entry_at))
    if status:
        query = query.where(Trade.status == status)
    if symbol:
        query = query.where(Trade.symbol == symbol)
    if market_type:
        query = query.where(Trade.market_type == market_type)
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    trades = result.scalars().all()

    return {
        "trades": [_serialize_trade(t) for t in trades],
        "total": len(trades),
    }


@router.get("/open")
async def get_open_positions(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    query = select(Trade).where(
        Trade.user_id == user_id, Trade.status == "open"
    ).order_by(desc(Trade.entry_at))

    result = await db.execute(query)
    trades = result.scalars().all()

    return {"positions": [_serialize_trade(t) for t in trades], "count": len(trades)}


@router.get("/history")
async def get_trade_history(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    query = select(Trade).where(
        Trade.user_id == user_id, Trade.status == "closed"
    ).order_by(desc(Trade.exit_at))

    if start_date:
        query = query.where(Trade.exit_at >= datetime.fromisoformat(start_date))
    if end_date:
        query = query.where(Trade.exit_at <= datetime.fromisoformat(end_date))
    query = query.limit(limit)

    result = await db.execute(query)
    trades = result.scalars().all()

    return {"trades": [_serialize_trade(t) for t in trades], "total": len(trades)}


@router.get("/{trade_id}")
async def get_trade(
    trade_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    result = await db.execute(
        select(Trade).where(Trade.id == trade_id, Trade.user_id == user_id)
    )
    trade = result.scalars().first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return _serialize_trade(trade)


@router.post("/{trade_id}/close")
async def close_trade(
    trade_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    from ..services.execution_engine import execution_engine

    result = await db.execute(
        select(Trade).where(Trade.id == trade_id, Trade.user_id == user_id, Trade.status == "open")
    )
    trade = result.scalars().first()
    if not trade:
        raise HTTPException(status_code=404, detail="Open trade not found")

    # For now, use the entry_price as exit (would be current market price in production)
    close_result = await execution_engine.close_trade(
        db=db,
        trade_id=trade_id,
        exit_price=float(trade.entry_price),  # Would fetch current price
        exit_reason="manual_close",
    )

    if not close_result.get("success"):
        raise HTTPException(status_code=500, detail=close_result.get("error"))

    return close_result


def _serialize_trade(t: Trade) -> dict:
    return {
        "id": str(t.id),
        "symbol": t.symbol,
        "side": t.side,
        "direction": t.direction,
        "market_type": t.market_type,
        "exchange": t.exchange,
        "entry_price": float(t.entry_price) if t.entry_price else None,
        "exit_price": float(t.exit_price) if t.exit_price else None,
        "quantity": float(t.quantity) if t.quantity else None,
        "invested_value": float(t.invested_value) if t.invested_value else None,
        "profit_loss": float(t.profit_loss) if t.profit_loss else None,
        "profit_loss_pct": float(t.profit_loss_pct) if t.profit_loss_pct else None,
        "fee": float(t.fee) if t.fee else None,
        "status": t.status,
        "alpha_score_at_entry": float(t.alpha_score_at_entry) if t.alpha_score_at_entry else None,
        "indicators_at_entry": t.indicators_at_entry,
        "take_profit_price": float(t.take_profit_price) if t.take_profit_price else None,
        "stop_loss_price": float(t.stop_loss_price) if t.stop_loss_price else None,
        "entry_at": t.entry_at.isoformat() if t.entry_at else None,
        "exit_at": t.exit_at.isoformat() if t.exit_at else None,
        "holding_seconds": t.holding_seconds,
    }
