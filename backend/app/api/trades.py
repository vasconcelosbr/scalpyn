"""Trades API — trade history, open positions, close trades."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional
from uuid import UUID

from ..database import get_db
from ..models.trade import Trade
from .config import get_current_user_id
from ..services.portfolio_service import portfolio_service
from ..services.trade_sync_service import trade_sync_service

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
    min_value_usdt: float = Query(10, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await portfolio_service.get_live_positions(
        db=db,
        user_id=user_id,
        min_value_usdt=min_value_usdt,
    )


@router.get("/history")
async def get_trade_history(
    period_days: Optional[int] = Query(None, ge=1, le=365),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    symbol: Optional[str] = None,
    market_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    query = select(Trade).where(
        Trade.user_id == user_id, Trade.status == "closed"
    ).order_by(desc(Trade.exit_at))

    parsed_start, parsed_end = portfolio_service.parse_history_window(
        period_days=period_days,
        start_date=start_date,
        end_date=end_date,
    )
    if parsed_start:
        query = query.where(Trade.exit_at >= parsed_start)
    if parsed_end:
        query = query.where(Trade.exit_at <= parsed_end)
    if symbol:
        query = query.where(Trade.symbol == symbol.upper())
    if market_type:
        query = query.where(Trade.market_type == market_type)
    query = query.limit(limit)

    result = await db.execute(query)
    trades = result.scalars().all()

    wins = [t for t in trades if _safe_num(t.profit_loss) > 0]
    total_pnl = sum(_safe_num(t.profit_loss) for t in trades)
    avg_profit_pct = (
        sum(_safe_num(t.profit_loss_pct) for t in trades) / len(trades)
        if trades else 0.0
    )

    return {
        "trades": [_serialize_trade(t) for t in trades],
        "total": len(trades),
        "summary": {
            "win_rate": round((len(wins) / len(trades) * 100.0) if trades else 0.0, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_profit_pct": round(avg_profit_pct, 2),
        },
    }


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


@router.post("/sync")
async def sync_trades_from_exchange(
    days: int = Query(90, ge=1),
    all_history: bool = Query(False, description="When true, fetches all trades since Gate.io launch (2017)"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Import closed spot orders from Gate.io into the trades table.

    Pass all_history=true to import the complete trade history (since 2017).
    Otherwise, pass days to control the lookback window (default 90).
    """
    result = await trade_sync_service.sync_spot_trades(
        db=db, user_id=user_id, days=days, all_history=all_history
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Sync failed"))
    return result


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
        "entry_price": float(t.entry_price) if t.entry_price is not None else None,
        "exit_price": float(t.exit_price) if t.exit_price is not None else None,
        "quantity": float(t.quantity) if t.quantity is not None else None,
        "invested_value": float(t.invested_value) if t.invested_value is not None else None,
        "profit_loss": float(t.profit_loss) if t.profit_loss is not None else None,
        "profit_loss_pct": float(t.profit_loss_pct) if t.profit_loss_pct is not None else None,
        "fee": float(t.fee) if t.fee is not None else None,
        "status": t.status,
        "alpha_score_at_entry": float(t.alpha_score_at_entry) if t.alpha_score_at_entry is not None else None,
        "indicators_at_entry": t.indicators_at_entry,
        "take_profit_price": float(t.take_profit_price) if t.take_profit_price is not None else None,
        "stop_loss_price": float(t.stop_loss_price) if t.stop_loss_price is not None else None,
        "entry_at": t.entry_at.isoformat() if t.entry_at else None,
        "exit_at": t.exit_at.isoformat() if t.exit_at else None,
        "holding_seconds": t.holding_seconds,
        "exchange_order_id": t.exchange_order_id,
        "source": t.source,
    }


def _safe_num(value: Optional[float]) -> float:
    if value is None:
        return 0.0
    return float(value)
