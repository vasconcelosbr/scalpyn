"""Performance API — institutional-grade history & PnL dashboard (Task #257).

Read-only endpoints over ``position_lifecycle``. Heavy ingestion endpoints
(``POST /sync``, ``POST /rebuild``) push data through Gate.io REST and the
FIFO engine; the GET endpoints never reach out to the exchange.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.executions_sync_service import executions_sync_service
from ..services.performance_service import performance_service
from ..services.position_lifecycle_service import position_lifecycle_service
from .config import get_current_user_id

router = APIRouter(prefix="/api/performance", tags=["Performance"])


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/summary")
async def get_summary(
    window: Optional[str] = Query("30D"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await performance_service.summary(
        db, user_id, window=window, from_dt=_parse_dt(from_), to_dt=_parse_dt(to),
    )


@router.get("/equity")
async def get_equity(
    window: Optional[str] = Query("30D"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await performance_service.equity_curve(
        db, user_id, window=window, from_dt=_parse_dt(from_), to_dt=_parse_dt(to),
    )


@router.get("/distribution")
async def get_distribution(
    window: Optional[str] = Query("30D"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await performance_service.distribution(
        db, user_id, window=window, from_dt=_parse_dt(from_), to_dt=_parse_dt(to),
    )


@router.get("/by-asset")
async def get_by_asset(
    window: Optional[str] = Query("30D"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await performance_service.by_asset(
        db, user_id, window=window, from_dt=_parse_dt(from_), to_dt=_parse_dt(to),
    )


@router.get("/executions")
async def get_executions(
    window: Optional[str] = Query("30D"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    symbol: Optional[str] = None,
    market_type: Optional[str] = None,
    direction: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = Query(None, description="Match against trade_id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = Query("closed_at_desc"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await performance_service.executions(
        db, user_id,
        window=window, from_dt=_parse_dt(from_), to_dt=_parse_dt(to),
        symbol=symbol, market_type=market_type,
        direction=direction, status=status, search=search,
        page=page, page_size=page_size, sort=sort,
    )


@router.get("/executions/{lifecycle_id}/fills")
async def get_fills(
    lifecycle_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await performance_service.fills_for_lifecycle(db, user_id, lifecycle_id)


@router.post("/sync")
async def sync_executions(
    days: int = Query(90, ge=1, le=365 * 5),
    markets: Optional[str] = Query("spot,futures",
        description="Comma-separated subset of {spot,futures}"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Pull fills from Gate.io REST and UPSERT them into ``exchange_executions``.

    After a successful pull, the FIFO engine is also invoked so the dashboard
    becomes consistent in a single round-trip.
    """
    market_list = [m.strip() for m in (markets or "").split(",") if m.strip()]
    sync_result = await executions_sync_service.backfill_user(
        db, user_id, days=days, markets=market_list or None,
    )
    if not sync_result.get("success"):
        raise HTTPException(status_code=400, detail=sync_result.get("error"))

    rebuild = await position_lifecycle_service.rebuild_for_user(db, user_id)
    if not rebuild.get("success"):
        raise HTTPException(status_code=500, detail=rebuild.get("error"))

    return {"sync": sync_result, "rebuild": rebuild}


@router.post("/rebuild")
async def rebuild_lifecycle(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Replay the FIFO engine over existing ``exchange_executions`` rows."""
    rebuild = await position_lifecycle_service.rebuild_for_user(db, user_id)
    if not rebuild.get("success"):
        raise HTTPException(status_code=500, detail=rebuild.get("error"))
    return rebuild
