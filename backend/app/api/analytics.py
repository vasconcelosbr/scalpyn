"""Analytics API — P&L charts, capital evolution, daily summary."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID
from datetime import datetime, timezone, timedelta

from ..database import get_db
from ..services.analytics_service import analytics_service
from ..services.portfolio_service import portfolio_service
from .config import get_current_user_id

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


@router.get("/pnl")
async def get_pnl(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None
    summary = await analytics_service.get_pnl_summary(db, user_id, start, end)
    return summary


@router.get("/capital")
async def get_capital_evolution(
    days: int = Query(30, ge=1, le=365),
    initial_capital: float = Query(100000, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    data = await analytics_service.get_capital_evolution(db, user_id, days, initial_capital)
    return {"data": data}


@router.get("/daily-summary")
async def get_daily_summary(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await analytics_service.get_daily_summary(db, user_id)


@router.get("/dashboard")
async def get_dashboard_overview(
    days: int = Query(30, ge=1, le=365),
    min_value_usdt: float = Query(10, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await portfolio_service.get_dashboard_overview(
        db=db,
        user_id=user_id,
        days=days,
        min_value_usdt=min_value_usdt,
    )
