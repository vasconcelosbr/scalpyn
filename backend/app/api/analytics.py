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


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO date/datetime string and ensure it is UTC-aware."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/pnl")
async def get_pnl(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    summary = await analytics_service.get_pnl_summary(db, user_id, _parse_dt(start_date), _parse_dt(end_date))
    return summary


@router.get("/capital")
async def get_capital_evolution(
    days: int = Query(30, ge=1),
    initial_capital: float = Query(100000, ge=0),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    data = await analytics_service.get_capital_evolution(
        db, user_id, days=days, initial_capital=initial_capital,
        start_date=_parse_dt(start_date), end_date=_parse_dt(end_date),
    )
    return {"data": data}


@router.get("/daily-summary")
async def get_daily_summary(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await analytics_service.get_daily_summary(db, user_id)


@router.get("/dashboard")
async def get_dashboard_overview(
    days: int = Query(30, ge=1),
    min_value_usdt: float = Query(10, ge=0),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await portfolio_service.get_dashboard_overview(
        db=db,
        user_id=user_id,
        days=days,
        min_value_usdt=min_value_usdt,
        start_date=_parse_dt(start_date),
        end_date=_parse_dt(end_date),
    )
