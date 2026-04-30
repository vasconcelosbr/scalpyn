"""Decision log API."""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
from datetime import date, datetime, time, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.backoffice import DecisionLog
from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from ..services.config_service import config_service
from ..services.seed_service import DEFAULT_DECISION_LOG
from .config import get_current_user_id

_MARKET_MODES = {"all", "spot", "futures"}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/decisions", tags=["Decisions"])

_DECISION_VALUES = {"ALLOW", "BLOCK", "ALL"}


def _serialize_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(value: Optional[str], *, is_end: bool = False) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            parsed = datetime.combine(parsed_date, time.max if is_end else time.min)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid datetime: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sanitize_symbol(symbol: Optional[str]) -> Optional[str]:
    if not symbol:
        return None
    cleaned = symbol.strip().upper()
    return cleaned or None


def _sanitize_strategy(strategy: Optional[str]) -> Optional[str]:
    if not strategy:
        return None
    cleaned = strategy.strip().upper()
    return cleaned or None


def _sanitize_decision(decision: Optional[str]) -> str:
    value = (decision or "ALL").strip().upper()
    if value not in _DECISION_VALUES:
        raise HTTPException(status_code=422, detail="decision must be ALLOW, BLOCK, or ALL")
    return value


def _encode_cursor(row: DecisionLog) -> str:
    payload = {
        "created_at": _serialize_dt(row.created_at),
        "id": row.id,
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor: Optional[str]) -> tuple[Optional[datetime], Optional[int]]:
    if not cursor:
        return None, None
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
        payload = json.loads(decoded)
        created_at = _parse_iso_datetime(payload.get("created_at"))
        row_id = int(payload.get("id"))
        return created_at, row_id
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc


def _serialize_item(row: DecisionLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "strategy": row.strategy,
        "timeframe": row.timeframe,
        "score": row.score,
        "decision": row.decision,
        "l1_pass": row.l1_pass,
        "l2_pass": row.l2_pass,
        "l3_pass": row.l3_pass,
        "reasons": row.reasons or {},
        "metrics": row.metrics or {},
        "latency_ms": row.latency_ms,
        "direction": row.direction,
        "event_type": row.event_type,
        "created_at": _serialize_dt(row.created_at),
    }


async def _get_decision_log_settings(db: AsyncSession, user_id: UUID) -> dict[str, Any]:
    try:
        config = await config_service.get_config(db, "decision_log", user_id)
    except Exception:
        config = {}
    return {
        **DEFAULT_DECISION_LOG,
        **(config if isinstance(config, dict) else {}),
    }


def _build_filters(
    *,
    user_id: UUID,
    start_date: Optional[str],
    end_date: Optional[str],
    symbol: Optional[str],
    strategy: Optional[str],
    score_min: Optional[float],
    score_max: Optional[float],
    decision: str,
) -> list[Any]:
    conditions: list[Any] = [DecisionLog.user_id == user_id]

    normalized_symbol = _sanitize_symbol(symbol)
    normalized_strategy = _sanitize_strategy(strategy)
    start_dt = _parse_iso_datetime(start_date)
    end_dt = _parse_iso_datetime(end_date, is_end=True)

    if normalized_symbol:
        conditions.append(DecisionLog.symbol == normalized_symbol)
    if normalized_strategy:
        conditions.append(DecisionLog.strategy == normalized_strategy)
    if score_min is not None:
        conditions.append(DecisionLog.score >= score_min)
    if score_max is not None:
        conditions.append(DecisionLog.score <= score_max)
    if start_dt is not None:
        conditions.append(DecisionLog.created_at >= start_dt)
    if end_dt is not None:
        conditions.append(DecisionLog.created_at <= end_dt)
    if decision != "ALL":
        conditions.append(DecisionLog.decision == decision)

    return conditions


async def _fetch_decisions(
    db: AsyncSession,
    *,
    user_id: UUID,
    start_date: Optional[str],
    end_date: Optional[str],
    symbol: Optional[str],
    strategy: Optional[str],
    score_min: Optional[float],
    score_max: Optional[float],
    decision: Optional[str],
    limit: Optional[int],
    cursor: Optional[str],
    settings: dict[str, Any],
) -> dict[str, Any]:
    normalized_decision = _sanitize_decision(decision)
    default_limit = int(settings.get("page_size") or DEFAULT_DECISION_LOG["page_size"])
    max_limit = int(settings.get("max_page_size") or DEFAULT_DECISION_LOG["max_page_size"])
    requested_limit = limit if limit is not None else default_limit
    safe_limit = max(1, min(requested_limit, max_limit))
    filters = _build_filters(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        symbol=symbol,
        strategy=strategy,
        score_min=score_min,
        score_max=score_max,
        decision=normalized_decision,
    )
    cursor_created_at, cursor_id = _decode_cursor(cursor)

    query = select(DecisionLog).where(and_(*filters))
    if cursor_created_at is not None and cursor_id is not None:
        query = query.where(
            or_(
                DecisionLog.created_at < cursor_created_at,
                and_(
                    DecisionLog.created_at == cursor_created_at,
                    DecisionLog.id < cursor_id,
                ),
            )
        )

    query = query.order_by(desc(DecisionLog.created_at), desc(DecisionLog.id)).limit(safe_limit + 1)
    result = await db.execute(query)
    rows = result.scalars().all()

    next_cursor = None
    if len(rows) > safe_limit:
        next_cursor = _encode_cursor(rows[safe_limit - 1])
        rows = rows[:safe_limit]

    return {
        "items": [_serialize_item(row) for row in rows],
        "next_cursor": next_cursor,
    }


@router.get("")
async def get_decisions(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    score_min: float = Query(0, ge=0, le=100),
    score_max: float = Query(100, ge=0, le=100),
    decision: str = Query("ALL"),
    limit: Optional[int] = Query(None, ge=1),
    cursor: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        if score_min > score_max:
            raise HTTPException(status_code=422, detail="score_min cannot be greater than score_max")
        settings = await _get_decision_log_settings(db, user_id)
        return await _fetch_decisions(
            db,
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            symbol=symbol,
            strategy=strategy,
            score_min=score_min,
            score_max=score_max,
            decision=decision,
            limit=limit,
            cursor=cursor,
            settings=settings,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch decisions: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch decisions") from exc


@router.get("/summary")
async def get_decisions_summary(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    score_min: float = Query(0, ge=0, le=100),
    score_max: float = Query(100, ge=0, le=100),
    decision: str = Query("ALL"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        if score_min > score_max:
            raise HTTPException(status_code=422, detail="score_min cannot be greater than score_max")

        normalized_decision = _sanitize_decision(decision)
        filters = _build_filters(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            symbol=symbol,
            strategy=strategy,
            score_min=score_min,
            score_max=score_max,
            decision=normalized_decision,
        )

        stats_query = select(
            func.count(DecisionLog.id).label("total"),
            func.avg(DecisionLog.score).label("avg_score"),
            func.sum(case((DecisionLog.decision == "ALLOW", 1), else_=0)).label("allowed"),
            func.sum(case((DecisionLog.l1_pass.is_(True), 1), else_=0)).label("l1_count"),
            func.sum(case((DecisionLog.l2_pass.is_(True), 1), else_=0)).label("l2_count"),
            func.sum(case((DecisionLog.l3_pass.is_(True), 1), else_=0)).label("l3_count"),
            func.avg(DecisionLog.latency_ms).label("avg_latency_ms"),
        ).where(and_(*filters))

        row = (await db.execute(stats_query)).one()
        total = int(row.total or 0)
        allowed = int(row.allowed or 0)

        return {
            "total_analyzed": total,
            "approval_rate": round((allowed / total) * 100, 2) if total else 0.0,
            "average_score": round(float(row.avg_score or 0), 2),
            "average_latency_ms": round(float(row.avg_latency_ms or 0), 2),
            "dropoff": {
                "l1_pass": int(row.l1_count or 0),
                "l2_pass": int(row.l2_count or 0),
                "l3_pass": int(row.l3_count or 0),
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch decision summary: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch decision summary") from exc


def _resolve_snapshot_score(asset: PipelineWatchlistAsset, market_mode: str) -> Optional[float]:
    """Pick the score that matches the asset's mode + futures direction."""
    alpha = float(asset.alpha_score) if asset.alpha_score is not None else None
    sl = float(asset.score_long) if asset.score_long is not None else None
    ss = float(asset.score_short) if asset.score_short is not None else None
    if (market_mode or "spot") != "futures":
        return alpha
    direction = (asset.futures_direction or "").upper()
    if direction == "LONG":
        return sl
    if direction == "SHORT":
        return ss
    candidates = [v for v in (sl, ss) if v is not None]
    return max(candidates) if candidates else alpha


def _build_snapshot_item(
    asset: PipelineWatchlistAsset,
    watchlist: PipelineWatchlist,
) -> dict[str, Any]:
    snapshot = asset.analysis_snapshot or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    details = snapshot.get("details") if isinstance(snapshot.get("details"), dict) else {}
    indicators = list(details.get("indicators") or snapshot.get("indicators") or [])
    score_rules = list(snapshot.get("score_rules") or [])
    market_mode = (watchlist.market_mode or "spot").lower()
    direction_raw = (asset.futures_direction or "").upper() if market_mode == "futures" else ""
    direction = direction_raw if direction_raw in {"LONG", "SHORT", "NEUTRAL"} else None
    return {
        "symbol": asset.symbol,
        "score": _resolve_snapshot_score(asset, market_mode),
        "alpha_score": float(asset.alpha_score) if asset.alpha_score is not None else None,
        "score_long": float(asset.score_long) if asset.score_long is not None else None,
        "score_short": float(asset.score_short) if asset.score_short is not None else None,
        "direction": direction,
        "watchlist_id": str(watchlist.id),
        "watchlist_name": watchlist.name,
        "stage": watchlist.level,
        "market_mode": market_mode,
        "approved_at": _serialize_dt(asset.refreshed_at or asset.entered_at),
        "indicators": indicators,
        "score_rules": score_rules,
    }


@router.get("/approved-snapshot")
async def get_approved_snapshot(
    symbol: Optional[str] = Query(None),
    market_mode: str = Query("all"),
    watchlist_id: Optional[UUID] = Query(None),
    sort: str = Query("score_desc"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Snapshot of all currently L3-approved assets across the user's pipeline.

    This is **not** the audit trail (decisions_log). It returns the current
    active set: every asset still sitting at L3 in any of the user's
    pipeline watchlists (level_direction NULL or "up"). Useful when the user
    wants to see "what is approved right now" instead of "what changed".
    """
    try:
        normalized_mode = (market_mode or "all").strip().lower()
        if normalized_mode not in _MARKET_MODES:
            raise HTTPException(
                status_code=422,
                detail="market_mode must be all, spot, or futures",
            )
        normalized_sort = (sort or "score_desc").strip().lower()
        if normalized_sort not in {"score_desc", "score_asc", "symbol_asc", "approved_at_desc"}:
            raise HTTPException(
                status_code=422,
                detail="sort must be one of: score_desc, score_asc, symbol_asc, approved_at_desc",
            )
        normalized_symbol = _sanitize_symbol(symbol)

        conditions = [
            PipelineWatchlist.user_id == user_id,
            func.upper(PipelineWatchlist.level) == "L3",
            or_(
                PipelineWatchlistAsset.level_direction.is_(None),
                PipelineWatchlistAsset.level_direction == "up",
            ),
        ]
        if normalized_mode in {"spot", "futures"}:
            conditions.append(PipelineWatchlist.market_mode == normalized_mode)
        if watchlist_id is not None:
            conditions.append(PipelineWatchlist.id == watchlist_id)
        if normalized_symbol:
            conditions.append(PipelineWatchlistAsset.symbol == normalized_symbol)

        query = (
            select(PipelineWatchlistAsset, PipelineWatchlist)
            .join(
                PipelineWatchlist,
                PipelineWatchlist.id == PipelineWatchlistAsset.watchlist_id,
            )
            .where(and_(*conditions))
        )

        result = await db.execute(query)
        rows = result.all()

        items = [_build_snapshot_item(asset, wl) for asset, wl in rows]

        if normalized_sort == "score_desc":
            items.sort(key=lambda i: (i["score"] is None, -(i["score"] or 0), i["symbol"]))
        elif normalized_sort == "score_asc":
            items.sort(key=lambda i: (i["score"] is None, (i["score"] or 0), i["symbol"]))
        elif normalized_sort == "symbol_asc":
            items.sort(key=lambda i: i["symbol"])
        else:  # approved_at_desc
            items.sort(key=lambda i: (i["approved_at"] is None, i["approved_at"] or ""), reverse=True)

        return {
            "items": items,
            "total": len(items),
            "as_of": _serialize_dt(datetime.now(timezone.utc)),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch approved snapshot: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch approved snapshot") from exc


@router.get("/approved-snapshot/watchlists")
async def list_approved_snapshot_watchlists(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """List the user's L3 watchlists for the snapshot tab filter dropdown."""
    try:
        result = await db.execute(
            select(
                PipelineWatchlist.id,
                PipelineWatchlist.name,
                PipelineWatchlist.market_mode,
            )
            .where(
                PipelineWatchlist.user_id == user_id,
                func.upper(PipelineWatchlist.level) == "L3",
            )
            .order_by(PipelineWatchlist.name.asc())
        )
        rows = result.all()
        return {
            "items": [
                {
                    "id": str(row.id),
                    "name": row.name,
                    "market_mode": (row.market_mode or "spot").lower(),
                }
                for row in rows
            ],
        }
    except Exception as exc:
        logger.error("Failed to list snapshot watchlists: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list watchlists") from exc


@router.get("/export")
async def export_decisions(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    score_min: float = Query(0, ge=0, le=100),
    score_max: float = Query(100, ge=0, le=100),
    decision: str = Query("ALL"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        if score_min > score_max:
            raise HTTPException(status_code=422, detail="score_min cannot be greater than score_max")

        normalized_decision = _sanitize_decision(decision)
        filters = _build_filters(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            symbol=symbol,
            strategy=strategy,
            score_min=score_min,
            score_max=score_max,
            decision=normalized_decision,
        )

        query = select(DecisionLog).where(and_(*filters)).order_by(desc(DecisionLog.created_at), desc(DecisionLog.id))
        result = await db.execute(query)
        rows = result.scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id",
            "symbol",
            "strategy",
            "timeframe",
            "score",
            "decision",
            "l1_pass",
            "l2_pass",
            "l3_pass",
            "latency_ms",
            "created_at",
            "reasons",
            "metrics",
        ])
        for row in rows:
            writer.writerow([
                row.id,
                row.symbol,
                row.strategy,
                row.timeframe,
                row.score,
                row.decision,
                row.l1_pass,
                row.l2_pass,
                row.l3_pass,
                row.latency_ms,
                _serialize_dt(row.created_at),
                json.dumps(row.reasons or {}, ensure_ascii=False),
                json.dumps(row.metrics or {}, ensure_ascii=False),
            ])

        output.seek(0)
        filename = f"decisions_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to export decisions: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to export decisions") from exc
