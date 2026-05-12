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
    market_mode: Optional[str] = Query(None, description="spot | futures"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    caller_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """List the caller's L3-approved assets straight from the pipeline.

    Reads ``pipeline_watchlist_assets`` joined with ``pipeline_watchlists``
    where ``level='L3'`` (case-insensitive) — the same source the
    ``/watchlist`` page L3 tab already renders. Unlike
    ``/api/diagnostics/decisions?status=APPROVED`` (which only has rows
    when ``execute_buy`` actually fired), this endpoint reflects every
    symbol that has cleared the L3 gates in the most recent pipeline
    scan, regardless of execution / balance.

    A recursive CTE walks back through ``source_watchlist_id`` so the
    root ``source_pool_id`` is resolved even on long chains
    (POOL → L1 → L2 → L3). When the chain's root pool is inactive the
    asset is dropped — we always scope to the caller's "pool ativo".

    Also returns ``min_trade_usdt`` at the top level so the UI can
    show an "aguardando saldo" badge without a second config call:
    sourced from the caller's ``spot_engine`` config
    (``buying.capital_per_trade_min_usdt``); falls back to ``10.0``
    if no spot_engine config exists yet (matches the spec's $10
    default).
    """
    mode_filter = None
    if market_mode and market_mode.lower() in {"spot", "futures"}:
        mode_filter = market_mode.lower()

    # Strict scope: require a resolved root pool that is currently
    # active. Drops rows where the chain back to ``source_pool_id`` is
    # broken (orphan watchlist) OR the root pool was paused — both
    # mean "not part of the user's pool ativo" per the task spec.
    where = [
        "pw.user_id = :caller_id",
        "UPPER(pw.level) = 'L3'",
        "r.source_pool_id IS NOT NULL",
        "p.is_active = TRUE",
    ]
    params: Dict[str, Any] = {"caller_id": str(caller_id), "limit": limit}
    if mode_filter:
        where.append("pw.market_mode = :market_mode")
        params["market_mode"] = mode_filter
    where_sql = " WHERE " + " AND ".join(where)

    # Recursive CTE: starting from each L3 watchlist, walk source_watchlist_id
    # upward until we hit the watchlist whose source_pool_id is set. That
    # source_pool_id is the chain's root pool. Cycle protection: depth cap
    # at 8 (POOL→L1→L2→L3 is 4 hops; 8 is double the worst legitimate case
    # and prevents an accidental SET NULL → self-reference loop from
    # spinning forever).
    sql = text(f"""
        WITH RECURSIVE chain(wl_id, current_id, source_pool_id, source_watchlist_id, depth) AS (
            SELECT id, id, source_pool_id, source_watchlist_id, 0
              FROM pipeline_watchlists
             WHERE user_id = :caller_id AND UPPER(level) = 'L3'
            UNION ALL
            SELECT c.wl_id, pw2.id, pw2.source_pool_id, pw2.source_watchlist_id, c.depth + 1
              FROM chain c
              JOIN pipeline_watchlists pw2 ON pw2.id = c.source_watchlist_id
             WHERE c.source_pool_id IS NULL
               AND c.source_watchlist_id IS NOT NULL
               AND c.depth < 8
        ),
        roots AS (
            SELECT DISTINCT ON (wl_id) wl_id, source_pool_id
              FROM chain
             WHERE source_pool_id IS NOT NULL
             ORDER BY wl_id, depth DESC
        )
        SELECT pwa.symbol                      AS symbol,
               pwa.alpha_score                 AS score,
               pwa.score_long                  AS score_long,
               pwa.score_short                 AS score_short,
               pwa.confidence_score            AS confidence_score,
               pwa.futures_direction           AS futures_direction,
               pw.market_mode                  AS market_type,
               COALESCE(pwa.refreshed_at, pwa.entered_at) AS approved_at,
               r.source_pool_id                AS pool_id,
               pw.id                           AS watchlist_id,
               pw.name                         AS watchlist_name
          FROM pipeline_watchlist_assets pwa
          JOIN pipeline_watchlists pw ON pw.id = pwa.watchlist_id
          LEFT JOIN roots r           ON r.wl_id = pw.id
          LEFT JOIN pools p           ON p.id = r.source_pool_id
        {where_sql}
        ORDER BY COALESCE(
            pwa.alpha_score,
            GREATEST(COALESCE(pwa.score_long, 0), COALESCE(pwa.score_short, 0))
        ) DESC NULLS LAST
        LIMIT :limit
    """)
    result = await db.execute(sql, params)
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
