"""Orders API — order history with filters."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional
from uuid import UUID

from ..database import get_db
from ..models.order import Order
from .config import get_current_user_id

router = APIRouter(prefix="/api/orders", tags=["Orders"])


@router.get("/")
async def get_orders(
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    query = select(Order).where(Order.user_id == user_id).order_by(desc(Order.created_at))
    if symbol:
        query = query.where(Order.symbol == symbol)
    if status:
        query = query.where(Order.status == status)
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    orders = result.scalars().all()

    return {
        "orders": [
            {
                "id": str(o.id),
                "trade_id": str(o.trade_id) if o.trade_id else None,
                "exchange_order_id": o.exchange_order_id,
                "symbol": o.symbol,
                "side": o.side,
                "order_type": o.order_type,
                "price": float(o.price) if o.price else None,
                "quantity": float(o.quantity) if o.quantity else None,
                "filled_quantity": float(o.filled_quantity) if o.filled_quantity else None,
                "status": o.status,
                "exchange": o.exchange,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
        "total": len(orders),
    }
