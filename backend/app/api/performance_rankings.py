"""Unified read-only ranking endpoints for Shadow Portfolio and L3 consumers."""

from datetime import date, datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas.profile_performance import (
    ProfileDailyPerformanceResponse,
    ProfileDailyRange,
    ProfilePerformanceResponse,
)
from ..schemas.shadow_trade import ProfileReportRow
from ..services.profile_performance_service import (
    get_profile_daily_performance,
    get_profile_performance,
)
from ..services.watchlist_performance_ranking_service import (
    RankingConfigError,
    get_performance_rankings,
)
from .config import get_current_user_id


router = APIRouter(tags=["Watchlist Performance Ranking"])


async def _rankings(db: AsyncSession, user_id: UUID, level: str | None = None):
    try:
        return await get_performance_rankings(db, user_id, level=level)
    except RankingConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/shadow-portfolio/report", response_model=List[ProfileReportRow])
async def shadow_portfolio_report(
    order_by: str = Query("ev_score", pattern="^(ev_score|performance_priority)$"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    rows = await _rankings(db, user_id)
    if direction == "asc":
        rows.reverse()
    return rows


@router.get(
    "/api/shadow-portfolio/profile-performance",
    response_model=ProfilePerformanceResponse,
)
async def shadow_portfolio_profile_performance(
    as_of: date | None = Query(None),
    range_days: int = Query(7),
    profile_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> ProfilePerformanceResponse:
    """Daily profile monitor using the canonical ranking score and shadow data."""

    selected_day = as_of or datetime.now(timezone.utc).date()
    try:
        return await get_profile_performance(
            db,
            user_id,
            as_of=selected_day,
            range_days=range_days,
            profile_id=profile_id,
        )
    except RankingConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/api/shadow-portfolio/profile-performance/daily",
    response_model=ProfileDailyPerformanceResponse,
)
async def shadow_portfolio_profile_daily_performance(
    as_of: date | None = Query(None),
    range: ProfileDailyRange = Query("7d"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> ProfileDailyPerformanceResponse:
    """Daily L3 Win Rate and realized P&L from canonical shadow trades."""

    selected_day = as_of or datetime.now(timezone.utc).date()
    try:
        return await get_profile_daily_performance(
            db,
            user_id,
            as_of=selected_day,
            range_key=range,
        )
    except RankingConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/l3/watchlists", response_model=List[ProfileReportRow])
@router.get("/api/l3/candidates", response_model=List[ProfileReportRow])
@router.get("/api/l3/profiles", response_model=List[ProfileReportRow])
async def l3_performance_rankings(
    order_by: str = Query("performance_priority", pattern="^performance_priority$"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await _rankings(db, user_id, level="L3")
