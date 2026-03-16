"""Reports API — trade reports with indicator snapshots, metrics, CSV export."""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional
from uuid import UUID
import io
import csv

from ..database import get_db
from ..models.trade import Trade
from ..services.analytics_service import analytics_service
from .config import get_current_user_id

router = APIRouter(prefix="/api/reports", tags=["Reports"])


@router.get("/trades")
async def get_trade_reports(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Get trades with full indicator snapshots at entry."""
    query = select(Trade).where(
        Trade.user_id == user_id, Trade.status == "closed"
    ).order_by(desc(Trade.exit_at))

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
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Export trade history as CSV."""
    query = select(Trade).where(
        Trade.user_id == user_id, Trade.status == "closed"
    ).order_by(desc(Trade.exit_at)).limit(5000)

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
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Get performance metrics."""
    from datetime import datetime, timezone, timedelta
    start = datetime.now(timezone.utc) - timedelta(days=days)
    return await analytics_service.get_pnl_summary(db, user_id, start_date=start)
