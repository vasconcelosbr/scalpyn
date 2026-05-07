"""Reports API — trade reports with indicator snapshots, metrics, CSV export."""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional
from uuid import UUID
from datetime import datetime, timezone, timedelta
import io
import csv

from ..database import get_db
from ..models.trade import Trade
from ..services.analytics_service import analytics_service
from .config import get_current_user_id

router = APIRouter(prefix="/api/reports", tags=["Reports"])


def _parse_period(
    days: Optional[int],
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Convert period params to start/end datetime objects.

    Explicit start_date/end_date take priority over days.
    If only days is provided, start = now - days, end = None (open).
    """
    if start_date or end_date:
        start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc) if start_date else None
        end = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc) if end_date else None
        return start, end
    if days:
        return datetime.now(timezone.utc) - timedelta(days=days), None
    return None, None


@router.get("/trades")
async def get_trade_reports(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = Query(None, ge=1),
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Get trades with full indicator snapshots at entry."""
    start, end = _parse_period(days, start_date, end_date)

    query = select(Trade).where(
        Trade.user_id == user_id, Trade.status == "closed"
    ).order_by(desc(Trade.exit_at))

    if start:
        query = query.where(Trade.exit_at >= start)
    if end:
        query = query.where(Trade.exit_at <= end)
    if symbol:
        query = query.where(Trade.symbol == symbol)
    if direction:
        query = query.where(Trade.direction == direction)
    query = query.limit(limit)

    result = await db.execute(query)
    trades = result.scalars().all()

    reports = []
    for t in trades:
        indicators = t.indicators_at_entry or {}
        reports.append({
            "date": t.exit_at.isoformat() if t.exit_at else t.entry_at.isoformat(),
            "symbol": t.symbol,
            "direction": t.direction,
            "market_type": t.market_type,
            "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price) if t.exit_price else None,
            "profit_loss": float(t.profit_loss) if t.profit_loss else 0,
            "profit_loss_pct": float(t.profit_loss_pct) if t.profit_loss_pct else 0,
            "alpha_score": float(t.alpha_score_at_entry) if t.alpha_score_at_entry else None,
            "holding_seconds": t.holding_seconds,
            "rsi": indicators.get("rsi"),
            "adx": indicators.get("adx"),
            "di_plus": indicators.get("di_plus"),
            "di_minus": indicators.get("di_minus"),
            "ema9": indicators.get("ema9"),
            "ema50": indicators.get("ema50"),
            "ema200": indicators.get("ema200"),
            "atr": indicators.get("atr"),
            "atr_pct": indicators.get("atr_pct"),
            "macd": indicators.get("macd"),
            "volume_spike": indicators.get("volume_spike"),
            "vwap_distance_pct": indicators.get("vwap_distance_pct"),
        })

    return {"reports": reports, "total": len(reports)}


@router.get("/trades/export")
async def export_trades_csv(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Export trade history as CSV."""
    start, end = _parse_period(days, start_date, end_date)

    query = select(Trade).where(
        Trade.user_id == user_id, Trade.status == "closed"
    ).order_by(desc(Trade.exit_at)).limit(5000)

    if start:
        query = query.where(Trade.exit_at >= start)
    if end:
        query = query.where(Trade.exit_at <= end)

    result = await db.execute(query)
    trades = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "Symbol", "Direction", "Type", "Entry Price", "Exit Price",
        "Quantity", "Invested", "P&L ($)", "P&L (%)", "Score", "Holding (s)"
    ])

    for t in trades:
        writer.writerow([
            t.exit_at.isoformat() if t.exit_at else "",
            t.symbol, t.direction, t.market_type,
            float(t.entry_price), float(t.exit_price) if t.exit_price else "",
            float(t.quantity), float(t.invested_value),
            float(t.profit_loss) if t.profit_loss else 0,
            float(t.profit_loss_pct) if t.profit_loss_pct else 0,
            float(t.alpha_score_at_entry) if t.alpha_score_at_entry else "",
            t.holding_seconds or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=scalpyn_trades.csv"},
    )


@router.get("/metrics")
async def get_metrics(
    days: Optional[int] = Query(30, ge=1),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Get performance metrics for a date range.

    Explicit start_date/end_date take priority over days.
    """
    start, end = _parse_period(days, start_date, end_date)
    return await analytics_service.get_pnl_summary(db, user_id, start_date=start, end_date=end)
