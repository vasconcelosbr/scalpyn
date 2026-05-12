"""Trade decision diagnostics — read-only views over ``trade_decisions``.

These endpoints expose the audit log produced by
``app.services.decision_audit_service.record_decision`` so operators can
trace a buy decision end-to-end (L3 evaluation → execution gate →
exchange call) by ``trace_id``, or aggregate rejection reasons over a
time window.

All endpoints are additive read-only queries — they never mutate the
audit table, never call out to exchanges, and never touch the engine
configuration.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diagnostics", tags=["Trade Diagnostics"])


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([hdm])\s*$", re.IGNORECASE)


def _parse_since(since: str) -> timedelta:
    """Parse a compact duration like ``24h`` / ``7d`` / ``90m``.

    Falls back to 24 hours if the input is malformed so a typo in the
    query string never returns 500. The caller still gets a consistent
    window.
    """
    if not since:
        return timedelta(hours=24)
    match = _DURATION_RE.match(since)
    if not match:
        return timedelta(hours=24)
    qty = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "h":
        return timedelta(hours=qty)
    if unit == "d":
        return timedelta(days=qty)
    if unit == "m":
        return timedelta(minutes=qty)
    return timedelta(hours=24)


def _row_to_dict(row: Any) -> Dict[str, Any]:
    """Convert an SQLAlchemy Row mapping to a JSON-serializable dict."""
    mapping = row._mapping if hasattr(row, "_mapping") else dict(row)
    out: Dict[str, Any] = {}
    for k, v in mapping.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


@router.get("/decisions")
async def list_decisions(
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="APPROVED | REJECTED | SKIPPED"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    caller_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """List the caller's own decisions ordered by ``decided_at DESC``.

    Tenancy is enforced server-side: the result set is always filtered
    by ``user_id = <authenticated caller>``. There is intentionally NO
    ``user_id`` query parameter — cross-tenant reads of audit data
    (which embed ``indicators_snapshot``) would leak strategy state.
    Filters compose with AND.
    """
    where: List[str] = ["user_id = :caller_id"]
    params: Dict[str, Any] = {"caller_id": str(caller_id)}
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol
    if status:
        where.append("status = :status")
        params["status"] = status.upper()
    where_sql = " WHERE " + " AND ".join(where)
    params["limit"] = limit
    params["offset"] = offset

    sql = text(f"""
        SELECT id, trace_id, user_id, pool_id, symbol, market_type, exchange,
               status, stage, reason, blocking_rule, rule_details,
               rules_matched, rules_failed, rules_skipped, score_breakdown,
               indicators_snapshot, latency_ms, trade_id, decided_at
          FROM trade_decisions
        {where_sql}
        ORDER BY decided_at DESC
        LIMIT :limit OFFSET :offset
    """)
    result = await db.execute(sql, params)
    rows = [_row_to_dict(r) for r in result.fetchall()]
    return {
        "items": rows,
        "limit": limit,
        "offset": offset,
        "count": len(rows),
    }


@router.get("/decisions/{trace_id}")
async def get_trace_timeline(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
    caller_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Return every decision row sharing the same ``trace_id``.

    Tenancy enforced: the WHERE clause restricts to the caller's own
    rows so a known-trace-id from another user returns 404, not the
    other user's timeline. Ordered ascending by ``decided_at`` so the
    response reads as a natural timeline (L3 → execution gate →
    execution row).
    """
    if not trace_id or len(trace_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid trace_id")

    sql = text("""
        SELECT id, trace_id, user_id, pool_id, symbol, market_type, exchange,
               status, stage, reason, blocking_rule, rule_details,
               rules_matched, rules_failed, rules_skipped, score_breakdown,
               indicators_snapshot, latency_ms, trade_id, decided_at
          FROM trade_decisions
         WHERE trace_id = :trace_id
           AND user_id  = :caller_id
         ORDER BY decided_at ASC
    """)
    result = await db.execute(sql, {"trace_id": trace_id, "caller_id": str(caller_id)})
    rows = [_row_to_dict(r) for r in result.fetchall()]
    if not rows:
        raise HTTPException(status_code=404, detail="trace_id not found")
    return {"trace_id": trace_id, "timeline": rows, "count": len(rows)}


@router.get("/l3-queue")
async def list_l3_queue(
    db: AsyncSession = Depends(get_db),
    caller_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """List the caller's L3-approved assets straight from the pipeline.

    Mirrors the exact query the watchlist page "Approved" tab uses
    against ``pipeline_watchlist_assets`` (see
    ``app.api.watchlists._load_active_watchlist_assets``): rows whose
    ``level_direction`` is NULL or ``'up'``, scoped to the caller's
    own L3 watchlists via ``pw.user_id = :user_id`` (tenancy).

    Also returns ``min_trade_usdt`` at the top level so the UI can
    show an "aguardando saldo" badge without a second config call:
    sourced from the caller's ``spot_engine`` config
    (``buying.capital_per_trade_min_usdt``); falls back to ``10.0``
    when no config row exists yet.
    """
    sql = text("""
        SELECT pwa.symbol,
               pwa.alpha_score   AS score,
               pwa.refreshed_at  AS approved_at,
               pw.id             AS watchlist_id,
               pw.name           AS watchlist_name
          FROM pipeline_watchlist_assets pwa
          JOIN pipeline_watchlists pw ON pw.id = pwa.watchlist_id
         WHERE pw.user_id = :user_id
           AND pw.level   = 'L3'
           AND (pwa.level_direction IS NULL OR pwa.level_direction = 'up')
         ORDER BY pwa.alpha_score DESC NULLS LAST
    """)
    result = await db.execute(sql, {"user_id": str(caller_id)})
    rows = [_row_to_dict(r) for r in result.fetchall()]

    # ── min_trade_usdt — read from caller's spot_engine config (same
    # field execute_buy uses via SpotCapitalManager). Avoid importing
    # the whole engine config schema for a single number; pluck the
    # path off the JSON directly. Falls back to 10.0 (spec default)
    # when the user has no config row yet — keeps the UI useful
    # before first onboarding.
    min_trade_usdt = 10.0
    try:
        cfg_row = (await db.execute(text("""
            SELECT config_json
              FROM config_profiles
             WHERE user_id = :uid
               AND config_type = 'spot_engine'
               AND is_active = TRUE
             LIMIT 1
        """), {"uid": str(caller_id)})).first()
        if cfg_row and cfg_row[0]:
            cfg = cfg_row[0] or {}
            buying = (cfg.get("buying") or {}) if isinstance(cfg, dict) else {}
            raw = buying.get("capital_per_trade_min_usdt")
            if raw is not None:
                min_trade_usdt = float(raw)
    except Exception as exc:  # noqa: BLE001 — never fail the listing on cfg lookup
        logger.warning(
            "l3-queue: spot_engine config lookup failed for %s: %s",
            caller_id, exc,
        )

    return {
        "items": rows,
        "count": len(rows),
        "min_trade_usdt": round(min_trade_usdt, 2),
    }


@router.get("/rejections/summary")
async def rejections_summary(
    pool_id: Optional[UUID] = Query(None),
    since: str = Query("24h", description="Window: e.g. 24h, 7d, 90m"),
    db: AsyncSession = Depends(get_db),
    caller_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Aggregate REJECTED + SKIPPED decisions by ``blocking_rule``.

    Tenancy enforced: always scoped to the authenticated caller. The
    optional ``pool_id`` narrows further to a single pool the caller
    owns. There is no ``user_id`` query parameter (see ``list_decisions``
    for the rationale).
    """
    window = _parse_since(since)
    cutoff = datetime.now(timezone.utc) - window

    where: List[str] = [
        "decided_at >= :cutoff",
        "status IN ('REJECTED', 'SKIPPED')",
        "user_id = :caller_id",
    ]
    params: Dict[str, Any] = {"cutoff": cutoff, "caller_id": str(caller_id)}
    if pool_id is not None:
        where.append("pool_id = :pool_id")
        params["pool_id"] = str(pool_id)
    where_sql = " WHERE " + " AND ".join(where)

    sql = text(f"""
        SELECT COALESCE(blocking_rule, reason, 'unknown') AS rule_key,
               status,
               COUNT(*) AS hits,
               COUNT(DISTINCT symbol) AS distinct_symbols,
               (ARRAY_AGG(DISTINCT symbol))[1:25] AS sample_symbols
          FROM trade_decisions
        {where_sql}
        GROUP BY rule_key, status
        ORDER BY hits DESC
        LIMIT 200
    """)
    result = await db.execute(sql, params)
    rows = [_row_to_dict(r) for r in result.fetchall()]
    return {
        "since": since,
        "window_seconds": int(window.total_seconds()),
        "cutoff": cutoff.isoformat(),
        "groups": rows,
        "count": len(rows),
    }
