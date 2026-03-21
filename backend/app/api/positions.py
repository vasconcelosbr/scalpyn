"""Positions API — aggregated open positions across spot and futures engines.

Routes:
  GET /api/positions/         → all open positions (spot + futures) from DB
  GET /api/positions/summary  → counts and unrealized PnL summary
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..api.config import get_current_user_id
from ..models.trade import Trade

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/positions", tags=["Positions"])


@router.get("/")
async def list_open_positions(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return all open positions for the authenticated user (spot + futures)."""
    result = await db.execute(
        select(Trade).where(
            Trade.user_id == user_id,
            Trade.status.in_(["open", "ACTIVE", "HOLDING_UNDERWATER"]),
        )
    )
    trades = result.scalars().all()

    positions = [
        {
            "id":                str(t.id),
            "symbol":            t.symbol,
            "side":              t.side,
            "direction":         t.direction,
            "market_type":       t.market_type,
            "entry_price":       float(t.entry_price) if t.entry_price else None,
            "quantity":          float(t.quantity) if t.quantity else None,
            "invested_value":    float(t.invested_value) if t.invested_value else None,
            "take_profit_price": float(t.take_profit_price) if t.take_profit_price else None,
            "stop_loss_price":   float(t.stop_loss_price) if t.stop_loss_price else None,
            "status":            t.status,
            "entry_at":          t.entry_at.isoformat() if t.entry_at else None,
        }
        for t in trades
    ]

    spot_positions    = [p for p in positions if p["market_type"] == "spot"]
    futures_positions = [p for p in positions if p["market_type"] == "futures"]

    return {
        "positions":         positions,
        "spot_positions":    spot_positions,
        "futures_positions": futures_positions,
        "total":             len(positions),
    }


@router.get("/summary")
async def positions_summary(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return a lightweight count/status summary of open positions."""
    result = await db.execute(
        select(Trade).where(
            Trade.user_id == user_id,
            Trade.status.in_(["open", "ACTIVE", "HOLDING_UNDERWATER"]),
        )
    )
    trades = result.scalars().all()

    spot_count    = sum(1 for t in trades if t.market_type == "spot")
    futures_count = sum(1 for t in trades if t.market_type == "futures")
    underwater    = sum(1 for t in trades if t.status == "HOLDING_UNDERWATER")

    return {
        "total":          len(trades),
        "spot":           spot_count,
        "futures":        futures_count,
        "underwater":     underwater,
    }
