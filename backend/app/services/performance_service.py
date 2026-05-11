"""Performance aggregation service — read-side queries over ``position_lifecycle``.

Task #257. All aggregations are pure SQL against ``position_lifecycle`` so we
never hit the exchange and never recompute PnL on the request path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _resolve_window(
    window: Optional[str],
    from_dt: Optional[datetime],
    to_dt: Optional[datetime],
) -> Tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if from_dt and to_dt:
        return from_dt, to_dt
    presets = {
        "1D": timedelta(days=1),
        "7D": timedelta(days=7),
        "30D": timedelta(days=30),
        "90D": timedelta(days=90),
        "MTD": None,
        "YTD": None,
        "ALL": timedelta(days=365 * 10),
    }
    key = (window or "30D").upper()
    if key == "MTD":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif key == "YTD":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        delta = presets.get(key, timedelta(days=30))
        start = now - delta
    return start, now


class PerformanceService:

    async def summary(
        self,
        db: AsyncSession,
        user_id: UUID,
        window: Optional[str] = "30D",
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        start, end = _resolve_window(window, from_dt, to_dt)
        prev_start = start - (end - start)

        params = {"uid": str(user_id), "start": start, "end": end, "prev_start": prev_start}

        agg = (await db.execute(text(
            """
            SELECT
                COUNT(*) FILTER (WHERE status='closed') AS total_closed,
                COUNT(*) FILTER (WHERE status='closed' AND pnl_usdt > 0) AS wins,
                COUNT(*) FILTER (WHERE status='closed' AND pnl_usdt < 0) AS losses,
                COUNT(*) FILTER (WHERE status='open')   AS open_count,
                COALESCE(SUM(CASE WHEN status='closed' THEN pnl_usdt END), 0)::float AS pnl_usdt,
                COALESCE(SUM(CASE WHEN status='closed' THEN invested_usdt END), 0)::float AS invested,
                COALESCE(SUM(CASE WHEN status='closed' THEN fees_total END), 0)::float AS fees,
                COALESCE(SUM(CASE WHEN status='closed' AND pnl_usdt > 0 THEN pnl_usdt END), 0)::float AS gross_win,
                COALESCE(SUM(CASE WHEN status='closed' AND pnl_usdt < 0 THEN pnl_usdt END), 0)::float AS gross_loss,
                COALESCE(AVG(CASE WHEN status='closed' AND pnl_usdt > 0 THEN pnl_usdt END), 0)::float AS avg_win,
                COALESCE(AVG(CASE WHEN status='closed' AND pnl_usdt < 0 THEN pnl_usdt END), 0)::float AS avg_loss,
                COALESCE(MAX(CASE WHEN status='closed' THEN pnl_usdt END), 0)::float AS biggest_win,
                COALESCE(MIN(CASE WHEN status='closed' THEN pnl_usdt END), 0)::float AS biggest_loss,
                COALESCE(AVG(CASE WHEN status='closed' THEN holding_seconds END), 0)::float AS avg_holding_s,
                COALESCE(SUM(CASE WHEN status='closed' THEN qty * avg_entry END), 0)::float AS volume_usdt,
                COALESCE(SUM(CASE WHEN status='closed' AND market_type='spot' THEN pnl_usdt END), 0)::float AS pnl_spot,
                COALESCE(SUM(CASE WHEN status='closed' AND market_type='futures' THEN pnl_usdt END), 0)::float AS pnl_futures
            FROM position_lifecycle
            WHERE user_id = :uid
              AND ( (status='closed' AND closed_at BETWEEN :start AND :end)
                 OR (status='open' AND opened_at <= :end) )
            """
        ), params)).one()

        prev = (await db.execute(text(
            """
            SELECT COALESCE(SUM(pnl_usdt), 0)::float AS pnl,
                   COUNT(*)::int AS total
            FROM position_lifecycle
            WHERE user_id = :uid AND status='closed'
              AND closed_at BETWEEN :prev_start AND :start
            """
        ), params)).one()

        invested = float(agg.invested or 0.0)
        roi = (float(agg.pnl_usdt) / invested) if invested > 0 else 0.0
        total_closed = int(agg.total_closed or 0)
        win_rate = (int(agg.wins or 0) / total_closed) if total_closed else 0.0
        gross_win = float(agg.gross_win or 0.0)
        gross_loss = abs(float(agg.gross_loss or 0.0))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

        # crude Sharpe — daily returns from closed_at bucket
        daily = (await db.execute(text(
            """
            SELECT date_trunc('day', closed_at) AS d,
                   SUM(pnl_usdt)::float AS pnl
            FROM position_lifecycle
            WHERE user_id = :uid AND status='closed'
              AND closed_at BETWEEN :start AND :end
            GROUP BY 1 ORDER BY 1
            """
        ), params)).all()
        sharpe: Optional[float] = None
        if len(daily) >= 2:
            xs = [float(r.pnl or 0.0) for r in daily]
            mean = sum(xs) / len(xs)
            var = sum((x - mean) ** 2 for x in xs) / len(xs)
            sd = var ** 0.5
            if sd > 0:
                sharpe = (mean / sd) * (252 ** 0.5)

        # drawdown from cumulative pnl curve
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        cur_dd = 0.0
        for r in daily:
            cum += float(r.pnl or 0.0)
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
            cur_dd = dd

        return {
            "window": {
                "key": (window or "30D").upper(),
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
            "capital": {
                "invested_usdt": round(invested, 2),
                "spot_pnl_usdt": round(float(agg.pnl_spot or 0.0), 2),
                "futures_pnl_usdt": round(float(agg.pnl_futures or 0.0), 2),
                "open_positions": int(agg.open_count or 0),
            },
            "pnl": {
                "total_usdt": round(float(agg.pnl_usdt or 0.0), 2),
                "roi_pct": round(roi * 100.0, 4),
                "fees_usdt": round(float(agg.fees or 0.0), 4),
                "delta_vs_previous": round(float(agg.pnl_usdt or 0.0) - float(prev.pnl or 0.0), 2),
            },
            "stats": {
                "total_trades": total_closed,
                "wins": int(agg.wins or 0),
                "losses": int(agg.losses or 0),
                "win_rate_pct": round(win_rate * 100.0, 2),
                "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
                "sharpe": round(sharpe, 3) if sharpe is not None else None,
                "avg_win_usdt": round(float(agg.avg_win or 0.0), 2),
                "avg_loss_usdt": round(float(agg.avg_loss or 0.0), 2),
                "biggest_win_usdt": round(float(agg.biggest_win or 0.0), 2),
                "biggest_loss_usdt": round(float(agg.biggest_loss or 0.0), 2),
                "avg_holding_seconds": int(agg.avg_holding_s or 0),
                "volume_usdt": round(float(agg.volume_usdt or 0.0), 2),
            },
            "risk": {
                "max_drawdown_usdt": round(max_dd, 2),
                "current_drawdown_usdt": round(cur_dd, 2),
                "recovery_pct": round(((cum - (peak - max_dd)) / max_dd * 100.0), 2)
                                 if max_dd > 0 else None,
            },
        }

    async def equity_curve(
        self, db: AsyncSession, user_id: UUID,
        window: Optional[str] = "30D",
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        start, end = _resolve_window(window, from_dt, to_dt)
        rows = (await db.execute(text(
            """
            SELECT date_trunc('day', closed_at) AS d,
                   COALESCE(SUM(pnl_usdt), 0)::float AS pnl
              FROM position_lifecycle
             WHERE user_id = :uid AND status='closed'
               AND closed_at BETWEEN :start AND :end
             GROUP BY 1 ORDER BY 1
            """
        ), {"uid": str(user_id), "start": start, "end": end})).all()

        curve: List[Dict[str, Any]] = []
        cum = 0.0
        peak = 0.0
        for r in rows:
            cum += float(r.pnl or 0.0)
            peak = max(peak, cum)
            curve.append({
                "date": r.d.isoformat() if r.d else None,
                "pnl_day": round(float(r.pnl or 0.0), 2),
                "cum_pnl": round(cum, 2),
                "drawdown": round(peak - cum, 2),
            })

        return {
            "window": {"from": start.isoformat(), "to": end.isoformat()},
            "points": curve,
        }

    async def distribution(
        self, db: AsyncSession, user_id: UUID,
        window: Optional[str] = "30D",
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        start, end = _resolve_window(window, from_dt, to_dt)
        params = {"uid": str(user_id), "start": start, "end": end}

        gl = (await db.execute(text(
            """
            SELECT
              COUNT(*) FILTER (WHERE pnl_usdt > 0) AS wins,
              COUNT(*) FILTER (WHERE pnl_usdt < 0) AS losses,
              COUNT(*) FILTER (WHERE market_type='spot') AS spot,
              COUNT(*) FILTER (WHERE market_type='futures') AS futures,
              COUNT(*) FILTER (WHERE direction='long') AS longs,
              COUNT(*) FILTER (WHERE direction='short') AS shorts
            FROM position_lifecycle
            WHERE user_id = :uid AND status='closed'
              AND closed_at BETWEEN :start AND :end
            """
        ), params)).one()

        heatmap = (await db.execute(text(
            """
            SELECT EXTRACT(DOW  FROM closed_at)::int AS dow,
                   EXTRACT(HOUR FROM closed_at)::int AS hr,
                   COUNT(*)::int AS n,
                   COALESCE(SUM(pnl_usdt), 0)::float AS pnl
              FROM position_lifecycle
             WHERE user_id = :uid AND status='closed'
               AND closed_at BETWEEN :start AND :end
             GROUP BY 1, 2
            """
        ), params)).all()

        return {
            "counts": {
                "wins": int(gl.wins or 0),
                "losses": int(gl.losses or 0),
                "spot": int(gl.spot or 0),
                "futures": int(gl.futures or 0),
                "longs": int(gl.longs or 0),
                "shorts": int(gl.shorts or 0),
            },
            "heatmap": [
                {"dow": r.dow, "hour": r.hr, "n": r.n, "pnl": round(r.pnl, 2)}
                for r in heatmap
            ],
        }

    async def by_asset(
        self, db: AsyncSession, user_id: UUID,
        window: Optional[str] = "30D",
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        start, end = _resolve_window(window, from_dt, to_dt)
        rows = (await db.execute(text(
            """
            SELECT
              symbol,
              market_type,
              COUNT(*)::int                                                  AS trades,
              COUNT(*) FILTER (WHERE pnl_usdt > 0)::int                      AS wins,
              COALESCE(SUM(pnl_usdt), 0)::float                              AS pnl,
              COALESCE(SUM(invested_usdt), 0)::float                         AS invested,
              COALESCE(SUM(fees_total), 0)::float                            AS fees,
              COALESCE(AVG(holding_seconds), 0)::float                       AS avg_holding
            FROM position_lifecycle
            WHERE user_id = :uid AND status='closed'
              AND closed_at BETWEEN :start AND :end
            GROUP BY symbol, market_type
            ORDER BY pnl DESC
            """
        ), {"uid": str(user_id), "start": start, "end": end})).all()

        out: List[Dict[str, Any]] = []
        for r in rows:
            invested = float(r.invested or 0.0)
            pnl = float(r.pnl or 0.0)
            out.append({
                "symbol": r.symbol,
                "market_type": r.market_type,
                "trades": int(r.trades),
                "win_rate_pct": round((int(r.wins) / int(r.trades) * 100.0) if r.trades else 0.0, 2),
                "pnl_usdt": round(pnl, 2),
                "fees_usdt": round(float(r.fees or 0.0), 4),
                "roi_pct": round((pnl / invested * 100.0) if invested > 0 else 0.0, 4),
                "avg_holding_seconds": int(r.avg_holding or 0),
            })
        return {"rows": out}

    async def executions(
        self, db: AsyncSession, user_id: UUID,
        window: Optional[str] = "30D",
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        symbol: Optional[str] = None,
        market_type: Optional[str] = None,
        direction: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
        sort: str = "closed_at_desc",
    ) -> Dict[str, Any]:
        start, end = _resolve_window(window, from_dt, to_dt)
        page = max(page, 1)
        page_size = max(min(page_size, 500), 1)
        offset = (page - 1) * page_size

        sort_map = {
            "closed_at_desc":  "closed_at DESC NULLS LAST",
            "closed_at_asc":   "closed_at ASC NULLS LAST",
            "pnl_desc":        "pnl_usdt DESC NULLS LAST",
            "pnl_asc":         "pnl_usdt ASC NULLS LAST",
            "holding_desc":    "holding_seconds DESC NULLS LAST",
            "holding_asc":     "holding_seconds ASC NULLS LAST",
            "symbol_asc":      "symbol ASC",
            "symbol_desc":     "symbol DESC",
        }
        order = sort_map.get(sort, sort_map["closed_at_desc"])

        where = ["user_id = :uid"]
        # Filter on closed_at for closed rows; for open positions surface
        # any position whose lifetime intersects the window (opened on or
        # before :end), so older still-open carry-overs aren't dropped
        # from narrower windows. Matches summary's open-position logic.
        where.append(
            "( (status='closed' AND closed_at BETWEEN :start AND :end) "
            "OR (status='open' AND opened_at <= :end) )"
        )
        params: Dict[str, Any] = {"uid": str(user_id), "start": start, "end": end}
        if symbol:
            where.append("symbol = :symbol")
            params["symbol"] = symbol.upper()
        if market_type:
            where.append("market_type = :mt")
            params["mt"] = market_type
        if direction:
            where.append("direction = :dir")
            params["dir"] = direction
        if status:
            where.append("status = :st")
            params["st"] = status
        if search:
            # Free-text match against entry/exit trade_ids JSONB arrays.
            # Uses `?` JSONB operator for exact element membership which
            # leverages a btree index path naturally.
            where.append(
                "(entry_trade_ids ? :search OR exit_trade_ids ? :search)"
            )
            params["search"] = search.strip()

        where_sql = " AND ".join(where)

        total = (await db.execute(text(
            f"SELECT COUNT(*)::int AS n FROM position_lifecycle WHERE {where_sql}"
        ), params)).scalar_one()

        rows = (await db.execute(text(
            f"""
            SELECT id, symbol, market_type, direction, opened_at, closed_at,
                   holding_seconds, qty, avg_entry, avg_exit,
                   invested_usdt, final_usdt, fees_total,
                   pnl_usdt, pnl_pct, roi, status,
                   n_fills_in, n_fills_out,
                   slippage_estimate, maker_taker_ratio, data_quality
              FROM position_lifecycle
             WHERE {where_sql}
             ORDER BY {order}
             LIMIT :limit OFFSET :offset
            """
        ), {**params, "limit": page_size, "offset": offset})).all()

        def _ser(r: Any) -> Dict[str, Any]:
            return {
                "id": r.id,
                "symbol": r.symbol,
                "market_type": r.market_type,
                "direction": r.direction,
                "opened_at": r.opened_at.isoformat() if r.opened_at else None,
                "closed_at": r.closed_at.isoformat() if r.closed_at else None,
                "holding_seconds": r.holding_seconds,
                "qty": float(r.qty) if r.qty is not None else None,
                "avg_entry": float(r.avg_entry) if r.avg_entry is not None else None,
                "avg_exit": float(r.avg_exit) if r.avg_exit is not None else None,
                "invested_usdt": float(r.invested_usdt) if r.invested_usdt is not None else None,
                "final_usdt": float(r.final_usdt) if r.final_usdt is not None else None,
                "fees_total": float(r.fees_total) if r.fees_total is not None else None,
                "pnl_usdt": float(r.pnl_usdt) if r.pnl_usdt is not None else None,
                "pnl_pct": float(r.pnl_pct) if r.pnl_pct is not None else None,
                "roi": float(r.roi) if r.roi is not None else None,
                "status": r.status,
                "n_fills_in": r.n_fills_in,
                "n_fills_out": r.n_fills_out,
                "slippage_estimate": float(r.slippage_estimate) if r.slippage_estimate is not None else None,
                "maker_taker_ratio": float(r.maker_taker_ratio) if r.maker_taker_ratio is not None else None,
                "data_quality": r.data_quality,
            }

        return {
            "page": page,
            "page_size": page_size,
            "total": int(total),
            "rows": [_ser(r) for r in rows],
        }

    async def fills_for_lifecycle(
        self, db: AsyncSession, user_id: UUID, lifecycle_id: int,
    ) -> Dict[str, Any]:
        meta = (await db.execute(text(
            """
            SELECT entry_trade_ids, exit_trade_ids, symbol, market_type
              FROM position_lifecycle
             WHERE id = :lid AND user_id = :uid
            """
        ), {"lid": lifecycle_id, "uid": str(user_id)})).one_or_none()
        if not meta:
            return {"rows": []}
        ids = list(meta.entry_trade_ids or []) + list(meta.exit_trade_ids or [])
        if not ids:
            return {"rows": []}
        rows = (await db.execute(text(
            """
            SELECT trade_id, order_id, side, role, price, quantity, fee,
                   fee_currency, executed_at
              FROM exchange_executions
             WHERE user_id = :uid
               AND market_type = :mt
               AND symbol = :symbol
               AND trade_id = ANY(:ids)
             ORDER BY executed_at ASC
            """
        ), {"uid": str(user_id), "mt": meta.market_type,
            "symbol": meta.symbol, "ids": ids})).all()
        return {
            "rows": [
                {
                    "trade_id": r.trade_id,
                    "order_id": r.order_id,
                    "side": r.side,
                    "role": r.role,
                    "price": float(r.price) if r.price is not None else None,
                    "quantity": float(r.quantity) if r.quantity is not None else None,
                    "fee": float(r.fee) if r.fee is not None else None,
                    "fee_currency": r.fee_currency,
                    "executed_at": r.executed_at.isoformat() if r.executed_at else None,
                }
                for r in rows
            ]
        }


performance_service = PerformanceService()
