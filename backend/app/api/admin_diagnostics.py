"""Admin diagnostics endpoints — read-only, bearer-token gated.

Use case
--------

Operators report assets stuck on "SEM DADOS / aguardando coleta" for
microstructure indicators (taker_ratio, volume_spike, volume_delta) while
the orderbook-derived ``spread_pct`` shows up fine. The data path for
those indicators spans four moving parts (pool_coins/pools tables,
microstructure scheduler, WS leader + Redis trade buffer, OHLCV REST
fetch, order_flow service), so debugging from logs alone is unreliable.

``GET /api/admin/symbol-health/{symbol}`` runs every probe in that data
path, in-process, and returns one JSON document that pinpoints which
stage is broken for the given symbol. All probes are read-only — no DB
writes, no Redis writes, no exchange-mutating calls.

Auth
----

Same pattern as ``/metrics`` (Task #167):

* ``ADMIN_DIAGNOSTICS_TOKEN`` env var **unset** → 404 (endpoint hidden).
* Env set, missing/wrong header → 401 with ``WWW-Authenticate: Bearer``.
* Env set, correct bearer → 200 with the diagnostic JSON.

The endpoint is also intended to live behind the same Cloud Run
``--ingress=internal-and-cloud-load-balancing`` perimeter as ``/metrics``
in production.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin Diagnostics"])


_BEARER_PREFIX = "Bearer "


def _expected_token() -> Optional[str]:
    token = os.environ.get("ADMIN_DIAGNOSTICS_TOKEN", "").strip()
    return token or None


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        return None
    return authorization[len(_BEARER_PREFIX):].strip() or None


def _enforce_auth(authorization: Optional[str]) -> None:
    expected = _expected_token()
    if expected is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    presented = _extract_bearer(authorization)
    if presented is None or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ─── Probes ─────────────────────────────────────────────────────────────────
# Every probe returns ``{"ok": True, ...}`` on success or
# ``{"ok": False, "error": "<class>: <msg>"}`` on failure. Probes never
# raise — the diagnostic endpoint must always return a complete document
# even when one subsystem is fully down.


def _err(exc: BaseException) -> Dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _age_seconds(ts: Optional[datetime]) -> Optional[float]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - ts).total_seconds(), 1)


async def _probe_pool_status(symbol: str) -> Dict[str, Any]:
    try:
        from ..database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(text("""
                SELECT pc.id            AS pool_coin_id,
                       pc.pool_id       AS pool_id,
                       pc.is_active     AS is_active,
                       pc.is_approved   AS is_approved,
                       p.market_type    AS market_type,
                       p.name           AS pool_name
                FROM pool_coins pc
                LEFT JOIN pools p ON p.id = pc.pool_id
                WHERE pc.symbol = :s
            """), {"s": symbol})).fetchall()
        memberships = [
            {
                "pool_coin_id": r.pool_coin_id,
                "pool_id": r.pool_id,
                "pool_name": r.pool_name,
                "market_type": r.market_type,
                "is_active": r.is_active,
                "is_approved": r.is_approved,
            }
            for r in rows
        ]
        return {
            "ok": True,
            "found": bool(memberships),
            "memberships": memberships,
            "any_approved_active_spot": any(
                m["is_active"]
                and m["is_approved"]
                and m["market_type"] == "spot"
                for m in memberships
            ),
        }
    except Exception as exc:
        logger.warning("[admin-diag] pool_status failed for %s: %s", symbol, exc)
        return _err(exc)


async def _probe_resolver_diff(symbol: str) -> Dict[str, Any]:
    try:
        from ..database import AsyncSessionLocal
        from ..services.gate_ws_leader import _resolve_spot_symbols
        from ..services.microstructure_scheduler_service import (
            _collect_symbols as _collect_micro,
        )

        ws_symbols = await _resolve_spot_symbols()
        async with AsyncSessionLocal() as db:
            micro_symbols = await _collect_micro(db)

        in_ws = symbol in ws_symbols
        in_micro = symbol in micro_symbols

        # Diff sizes — useful for the operator to see at a glance whether
        # the two universes are "kinda the same" (drift on one symbol)
        # or "wildly different" (resolver bug).
        ws_set = set(ws_symbols)
        micro_set = set(micro_symbols)

        return {
            "ok": True,
            "in_ws_subscription": in_ws,
            "in_microstructure_scheduler": in_micro,
            "drift_for_this_symbol": in_ws != in_micro,
            "drift_reason": (
                "in microstructure scheduler but NOT in WS subscription "
                "(no pool with market_type='spot' attached) — order_flow "
                "buffer will stay empty, only REST fallback will work"
                if in_micro and not in_ws
                else "in WS subscription but NOT in microstructure scheduler"
                "  — should be impossible, indicates resolver inversion bug"
                if in_ws and not in_micro
                else None
            ),
            "totals": {
                "ws_universe_size": len(ws_set),
                "microstructure_universe_size": len(micro_set),
                "only_in_ws": len(ws_set - micro_set),
                "only_in_microstructure": len(micro_set - ws_set),
            },
        }
    except Exception as exc:
        logger.warning("[admin-diag] resolver_diff failed for %s: %s", symbol, exc)
        return _err(exc)


async def _probe_trade_buffer(symbol: str) -> Dict[str, Any]:
    try:
        from ..exchange_adapters.gate_adapter import GateAdapter
        from ..services.redis_client import get_async_redis

        redis = await get_async_redis()
        if redis is None:
            return {
                "ok": True,
                "redis_available": False,
                "reason": "redis client unavailable (cooldown or init failure)",
            }

        normalized = GateAdapter._normalize_symbol(symbol)
        key = f"trades_buffer:spot:{normalized}"

        member_count = await redis.zcard(key)
        ttl = await redis.ttl(key)

        oldest_age_s: Optional[float] = None
        newest_age_s: Optional[float] = None
        if member_count and member_count > 0:
            now_ms = time.time() * 1000.0
            oldest = await redis.zrange(key, 0, 0, withscores=True)
            newest = await redis.zrange(key, -1, -1, withscores=True)
            if oldest:
                oldest_age_s = round((now_ms - float(oldest[0][1])) / 1000.0, 1)
            if newest:
                newest_age_s = round((now_ms - float(newest[0][1])) / 1000.0, 1)

        return {
            "ok": True,
            "redis_available": True,
            "key": key,
            "exists": bool(member_count and member_count > 0),
            "member_count": int(member_count or 0),
            "ttl_seconds": int(ttl) if ttl is not None and ttl >= 0 else None,
            "oldest_trade_age_seconds": oldest_age_s,
            "newest_trade_age_seconds": newest_age_s,
        }
    except Exception as exc:
        logger.warning("[admin-diag] trade_buffer failed for %s: %s", symbol, exc)
        return _err(exc)


async def _probe_indicators_history(symbol: str) -> Dict[str, Any]:
    try:
        from ..database import AsyncSessionLocal
        # Latest row per (scheduler_group, timeframe) so a stale or
        # crashed scheduler is not masked by a healthy one that wrote
        # more recently. Falls back to a global LIMIT 5 view as well
        # for at-a-glance recency.
        async with AsyncSessionLocal() as db:
            latest_per_group = (await db.execute(text("""
                SELECT DISTINCT ON (scheduler_group, timeframe)
                       scheduler_group,
                       timeframe,
                       time,
                       (SELECT array_agg(k ORDER BY k)
                          FROM jsonb_object_keys(indicators_json) k) AS keys
                FROM indicators
                WHERE symbol = :s
                ORDER BY scheduler_group, timeframe, time DESC
            """), {"s": symbol})).fetchall()

            recent_rows = (await db.execute(text("""
                SELECT time,
                       timeframe,
                       scheduler_group,
                       (SELECT array_agg(k ORDER BY k)
                          FROM jsonb_object_keys(indicators_json) k) AS keys
                FROM indicators
                WHERE symbol = :s
                ORDER BY time DESC
                LIMIT 5
            """), {"s": symbol})).fetchall()

        def _row(r) -> Dict[str, Any]:
            keys = list(r.keys or [])
            return {
                "time": r.time.isoformat() if r.time else None,
                "age_seconds": _age_seconds(r.time),
                "timeframe": r.timeframe,
                "scheduler_group": r.scheduler_group,
                "indicator_keys": keys,
                "has_taker_ratio": "taker_ratio" in keys,
                "has_volume_spike": "volume_spike" in keys,
                "has_volume_delta": "volume_delta" in keys,
                "has_spread_pct": "spread_pct" in keys,
            }

        return {
            "ok": True,
            "latest_per_scheduler_group": [_row(r) for r in latest_per_group],
            "rows": [_row(r) for r in recent_rows],
        }
    except Exception as exc:
        logger.warning("[admin-diag] indicators_history failed for %s: %s", symbol, exc)
        return _err(exc)


async def _probe_ohlcv_history(symbol: str) -> Dict[str, Any]:
    try:
        from ..database import AsyncSessionLocal
        results: Dict[str, Any] = {}
        async with AsyncSessionLocal() as db:
            for tf in ("5m", "1h"):
                row = (await db.execute(text("""
                    SELECT time, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = :s AND timeframe = :tf
                    ORDER BY time DESC
                    LIMIT 1
                """), {"s": symbol, "tf": tf})).fetchone()
                if row is None:
                    results[tf] = {"present": False}
                else:
                    results[tf] = {
                        "present": True,
                        "time": row.time.isoformat() if row.time else None,
                        "age_seconds": _age_seconds(row.time),
                        "close": float(row.close) if row.close is not None else None,
                        "volume": float(row.volume) if row.volume is not None else None,
                    }
        return {"ok": True, "by_timeframe": results}
    except Exception as exc:
        logger.warning("[admin-diag] ohlcv_history failed for %s: %s", symbol, exc)
        return _err(exc)


async def _probe_live_orderbook(symbol: str) -> Dict[str, Any]:
    try:
        from ..services.market_data_service import market_data_service
        payload = await market_data_service.fetch_orderbook_metrics(symbol)
        return {
            "ok": True,
            "spread_pct": payload.get("spread_pct"),
            "orderbook_depth_usdt": payload.get("orderbook_depth_usdt"),
            "source": payload.get("market_data_source"),
            "raw_keys": sorted(payload.keys()),
        }
    except Exception as exc:
        logger.warning("[admin-diag] live_orderbook failed for %s: %s", symbol, exc)
        return _err(exc)


async def _probe_live_ohlcv(symbol: str) -> Dict[str, Any]:
    try:
        from ..services.market_data_service import market_data_service
        df = await market_data_service.fetch_ohlcv(symbol, "5m", limit=100)
        if df is None or df.empty:
            return {"ok": True, "rows": 0, "exchange": None, "last_time": None}
        last_time = df["time"].iloc[-1] if "time" in df.columns else None
        # ``time`` may be epoch seconds, ms, or a datetime — normalise to ISO.
        last_iso: Optional[str] = None
        if last_time is not None:
            try:
                if isinstance(last_time, (int, float)):
                    secs = float(last_time) / 1000.0 if float(last_time) > 1e11 else float(last_time)
                    last_iso = datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
                else:
                    last_iso = str(last_time)
            except Exception:
                last_iso = str(last_time)
        return {
            "ok": True,
            "rows": int(len(df)),
            "exchange": df.attrs.get("exchange"),
            "last_time": last_iso,
        }
    except Exception as exc:
        logger.warning("[admin-diag] live_ohlcv failed for %s: %s", symbol, exc)
        return _err(exc)


async def _probe_live_order_flow(symbol: str) -> Dict[str, Any]:
    try:
        from ..exchange_adapters.gate_adapter import GateAdapter
        from ..services.order_flow_service import get_order_flow_data
        from ..services.redis_client import get_async_redis

        window_seconds = 300
        of = await get_order_flow_data(symbol, window_seconds=window_seconds)

        # Cross-reference: how many trades did the buffer hold inside
        # the same look-back window, vs. what the public dict reports
        # as its source. Pinpoints "fallback REST returned zero trades"
        # vs. "buffer empty so REST took over and succeeded" without
        # needing log access.
        buffer_trades_in_window: Optional[int] = None
        try:
            redis = await get_async_redis()
            if redis is not None:
                normalized = GateAdapter._normalize_symbol(symbol)
                key = f"trades_buffer:spot:{normalized}"
                cutoff_ms = (time.time() - window_seconds) * 1000.0
                buffer_trades_in_window = int(
                    await redis.zcount(key, cutoff_ms, "+inf")
                )
        except Exception as exc:
            logger.debug(
                "[admin-diag] live_order_flow buffer-count failed for %s: %s",
                symbol, exc,
            )

        source = of.get("taker_source") if of else None
        return {
            "ok": True,
            "taker_ratio": of.get("taker_ratio") if of else None,
            "buy_pressure": of.get("buy_pressure") if of else None,
            "volume_delta": of.get("volume_delta") if of else None,
            "taker_buy_volume": of.get("taker_buy_volume") if of else None,
            "taker_sell_volume": of.get("taker_sell_volume") if of else None,
            "source": source,
            "window": of.get("taker_window") if of else None,
            "buffer_trades_in_window": buffer_trades_in_window,
            "fallback_used": source == "gate_io_trades",
            "fallback_returned_zero_trades": (
                source == "gate_io_trades"
                and (of is None or of.get("taker_buy_volume") is None)
            ),
        }
    except Exception as exc:
        logger.warning("[admin-diag] live_order_flow failed for %s: %s", symbol, exc)
        return _err(exc)


async def _probe_ws_leader_status() -> Dict[str, Any]:
    try:
        from ..services.gate_ws_leader import (
            LEADER_KEY,
            LEADER_RENEW_INTERVAL_SECONDS,
            LEADER_TTL_SECONDS,
        )
        from ..services.redis_client import get_async_redis

        redis = await get_async_redis()
        if redis is None:
            return {
                "ok": True,
                "redis_available": False,
            }
        holder = await redis.get(LEADER_KEY)
        ttl = await redis.ttl(LEADER_KEY)
        ttl_int = int(ttl) if ttl is not None and ttl >= 0 else None

        # Each successful renew resets the TTL back to LEADER_TTL_SECONDS,
        # so (LEADER_TTL_SECONDS - current_ttl) is the seconds-since-last-
        # renew — i.e. the leader heartbeat age.
        heartbeat_age: Optional[int] = (
            max(LEADER_TTL_SECONDS - ttl_int, 0) if ttl_int is not None else None
        )
        # Renew runs every LEADER_RENEW_INTERVAL_SECONDS; we tolerate one
        # missed renew before flagging the leader as unhealthy.
        unhealthy = (
            heartbeat_age is not None
            and heartbeat_age > 2 * LEADER_RENEW_INTERVAL_SECONDS
        )

        return {
            "ok": True,
            "redis_available": True,
            "leader_holder": (
                holder.decode("utf-8", errors="replace")
                if isinstance(holder, (bytes, bytearray))
                else holder
            ),
            "leader_ttl_seconds": ttl_int,
            "leader_heartbeat_age_seconds": heartbeat_age,
            "leader_heartbeat_unhealthy": unhealthy,
            "renew_interval_seconds": LEADER_RENEW_INTERVAL_SECONDS,
            "lock_ttl_seconds": LEADER_TTL_SECONDS,
            "elected": holder is not None,
        }
    except Exception as exc:
        logger.warning("[admin-diag] ws_leader_status failed: %s", exc)
        return _err(exc)


# ─── Endpoint ───────────────────────────────────────────────────────────────


@router.get("/symbol-health/{symbol}", include_in_schema=False)
async def symbol_health(
    symbol: str,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Return a complete diagnostic snapshot for a single symbol.

    Every subsystem along the microstructure data path is probed
    independently. Failures in one probe are reported in-band as
    ``{"ok": false, "error": "..."}`` so that operators always see
    the full picture even when some downstream is down.
    """
    _enforce_auth(authorization)

    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    # Run probes concurrently — each one is independent and read-only.
    (
        pool_status,
        resolver_diff,
        trade_buffer,
        indicators_history,
        ohlcv_history,
        live_orderbook,
        live_ohlcv,
        live_order_flow,
        ws_leader_status,
    ) = await asyncio.gather(
        _probe_pool_status(sym),
        _probe_resolver_diff(sym),
        _probe_trade_buffer(sym),
        _probe_indicators_history(sym),
        _probe_ohlcv_history(sym),
        _probe_live_orderbook(sym),
        _probe_live_ohlcv(sym),
        _probe_live_order_flow(sym),
        _probe_ws_leader_status(),
    )

    return {
        "symbol": sym,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "pool_status": pool_status,
        "resolver_diff": resolver_diff,
        "trade_buffer": trade_buffer,
        "indicators_history": indicators_history,
        "ohlcv_history": ohlcv_history,
        "live_probes": {
            "orderbook_metrics": live_orderbook,
            "ohlcv_5m": live_ohlcv,
            "order_flow_300s": live_order_flow,
        },
        "ws_leader_status": ws_leader_status,
    }


# ─── P0 pipeline-integrity probes ───────────────────────────────────────────


async def _probe_empty_metrics(window_days: int = 7) -> Dict[str, Any]:
    """ALLOW decisions whose metrics JSONB is NULL or empty ({}).

    These rows land in decisions_log but carry no indicator snapshot,
    so any shadow trade derived from them will have an empty
    features_snapshot — making them worthless for ML training.
    """
    try:
        from ..database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            row = (await db.execute(text("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE metrics IS NULL OR metrics = '{}'::jsonb
                    )                                        AS empty_count,
                    COUNT(*)                                 AS total_count
                FROM decisions_log
                WHERE decision = 'ALLOW'
                  AND created_at > NOW() - INTERVAL ':days days'
            """.replace(":days days", f"{window_days} days")))).fetchone()
        empty = int(row.empty_count or 0)
        total = int(row.total_count or 0)
        rate = round(empty / total, 4) if total > 0 else 0.0
        return {
            "ok": True,
            "window_days": window_days,
            "empty_metrics_count": empty,
            "total_allow_count": total,
            "empty_metrics_rate": rate,
            "healthy": rate < 0.05,  # <5% threshold
        }
    except Exception as exc:
        logger.warning("[admin-diag] empty_metrics probe failed: %s", exc)
        return _err(exc)


async def _probe_orphaned_decisions(window_days: int = 7) -> Dict[str, Any]:
    """ALLOW decisions that have no corresponding shadow_trades row.

    When this count is high it means the async gap between
    _persist_decision_logs and _backfill_shadows_for_all_users is
    losing signals — trades are approved but never simulated, so their
    outcome never feeds ML training.
    """
    try:
        from ..database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            row = (await db.execute(text(f"""
                SELECT
                    COUNT(*) FILTER (WHERE st.id IS NULL) AS orphaned_count,
                    COUNT(*)                              AS total_count
                FROM decisions_log dl
                LEFT JOIN shadow_trades st ON st.decision_id = dl.id
                WHERE dl.decision = 'ALLOW'
                  AND dl.created_at > NOW() - INTERVAL '{window_days} days'
            """))).fetchone()
        orphaned = int(row.orphaned_count or 0)
        total = int(row.total_count or 0)
        rate = round(orphaned / total, 4) if total > 0 else 0.0
        return {
            "ok": True,
            "window_days": window_days,
            "orphaned_decisions_count": orphaned,
            "total_allow_count": total,
            "orphaned_rate": rate,
            "healthy": rate < 0.10,  # <10% threshold
        }
    except Exception as exc:
        logger.warning("[admin-diag] orphaned_decisions probe failed: %s", exc)
        return _err(exc)


async def _probe_pnl_null_rate() -> Dict[str, Any]:
    """Rate of ALLOW decisions where pnl_pct is still NULL.

    NULL pnl_pct means the trade was never closed (no TP/SL reached)
    or the outcome was never written back. These rows are dropped by
    build_training_dataframe() so they silently shrink the ML dataset.
    """
    try:
        from ..database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            row = (await db.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE pnl_pct IS NULL) AS null_count,
                    COUNT(*)                                 AS total_count
                FROM decisions_log
                WHERE decision = 'ALLOW'
            """))).fetchone()
        null_count = int(row.null_count or 0)
        total = int(row.total_count or 0)
        rate = round(null_count / total, 4) if total > 0 else 0.0
        return {
            "ok": True,
            "pnl_null_count": null_count,
            "total_allow_count": total,
            "pnl_null_rate": rate,
            "healthy": rate < 0.20,  # <20% threshold
        }
    except Exception as exc:
        logger.warning("[admin-diag] pnl_null_rate probe failed: %s", exc)
        return _err(exc)


@router.get("/diagnostics/pipeline-integrity", include_in_schema=False)
async def pipeline_integrity(
    window_days: int = 7,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """P0 pipeline integrity checks: empty metrics, orphaned decisions, pnl NULL rate.

    Runs three independent probes concurrently. Returns a ``healthy`` boolean
    that is True only when all three probes are within their thresholds:

    * ``empty_metrics_rate``    < 5%  (decisions_log ALLOW rows with no indicator snapshot)
    * ``orphaned_decisions``    < 10% (ALLOW decisions with no shadow_trades row)
    * ``pnl_null_rate``         < 20% (ALLOW decisions where pnl_pct never got written)

    All probes are read-only. Each reports its own ``healthy`` sub-flag so
    operators can see which check is failing.
    """
    _enforce_auth(authorization)

    empty_metrics, orphaned_decisions, pnl_null = await asyncio.gather(
        _probe_empty_metrics(window_days),
        _probe_orphaned_decisions(window_days),
        _probe_pnl_null_rate(),
    )

    all_ok = (
        empty_metrics.get("ok", False)
        and orphaned_decisions.get("ok", False)
        and pnl_null.get("ok", False)
    )
    all_healthy = (
        empty_metrics.get("healthy", False)
        and orphaned_decisions.get("healthy", False)
        and pnl_null.get("healthy", False)
    )

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "healthy": all_ok and all_healthy,
        "probes": {
            "empty_metrics": empty_metrics,
            "orphaned_decisions": orphaned_decisions,
            "pnl_null_rate": pnl_null,
        },
    }


# ─── P0 pnl backfill ────────────────────────────────────────────────────────


@router.post("/diagnostics/backfill-pnl", include_in_schema=False)
async def backfill_pnl(
    limit: int = 500,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Back-fill pnl_pct / outcome / holding_seconds into decisions_log for
    rows that were labelled NULL before the P0 writeback fix was deployed.

    Finds COMPLETED shadow_trades whose linked decisions_log row still has
    pnl_pct IS NULL and applies the outcome vocabulary mapping
    (TP_HIT→tp, SL_HIT→sl, TIMEOUT→timeout). Safe to call repeatedly —
    the UPDATE predicate (pnl_pct IS NULL) is idempotent.

    ``limit`` caps how many rows are processed per call (default 500).
    Call repeatedly until ``updated == 0`` to drain the full backlog.
    """
    _enforce_auth(authorization)

    from ..services.shadow_trade_service import backfill_decisions_log_pnl_from_shadows

    updated = await backfill_decisions_log_pnl_from_shadows(limit=limit)
    return {
        "ok": True,
        "updated": updated,
        "limit": limit,
        "done": updated == 0,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── Symbol audit (Task #194) ───────────────────────────────────────────────


class SymbolAuditRequest(BaseModel):
    """Body accepted by ``POST /api/admin/diagnostics/symbol-audit``.

    All fields are optional; the defaults run a full pool-universe audit
    in *active* mode with bulk approval enabled.
    """

    dry_run: bool = Field(
        default=False,
        description="Classify symbols and propose actions but do not mutate any state.",
    )
    no_approve: bool = Field(
        default=False,
        description="Skip the pool_coins.is_approved bulk UPDATE (refresh + recompute still run).",
    )
    symbols: Optional[List[str]] = Field(
        default=None,
        description="Restrict the audit to these symbols. Default: full pool universe.",
    )
    concurrency: int = Field(
        default=16, ge=1, le=64,
        description="Per-symbol probe concurrency.",
    )


@router.post("/diagnostics/symbol-audit", include_in_schema=False)
async def symbol_audit(
    payload: SymbolAuditRequest = Body(default_factory=SymbolAuditRequest),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Run the symbol-ingestion audit and (optionally) repair in batch.

    Returns the same JSON document as ``python -m scripts.symbol_health_audit
    --json`` so operators can use either tool interchangeably:

    * ``report``      — :class:`SymbolHealthReport` aggregate (counts + per-symbol).
    * ``remediation`` — :class:`RemediationReport` with the action plan
                        (and execution result when ``dry_run=False``).
    """
    _enforce_auth(authorization)

    # Lazy imports keep the admin module import-light when the audit is
    # never invoked — the symbol-health probes pull in the WS leader and
    # exchange adapters which we don't want to load on every request.
    from ..services.symbol_health_service import (
        SymbolHealthService,
        build_etapa8_envelope,
    )
    from ..services.symbol_remediator import GateSymbolValidator, SymbolRemediator

    health = SymbolHealthService(concurrency=payload.concurrency)
    report = await health.audit(symbols=payload.symbols)

    remediator = SymbolRemediator(
        validator=GateSymbolValidator(),
        approve_unknown=not payload.no_approve,
        recompute_indicators=True,
    )
    rem = await remediator.remediate(report, dry_run=payload.dry_run)

    # Etapa 8 of the prompt — the operator-facing envelope is the
    # contract; ``report`` and ``remediation`` are kept as nested debug
    # detail (back-compat with anything already integrating against the
    # old shape) but ``resumo``, ``lista`` and ``system_healthy`` are
    # what panels and the runbook key off of.
    envelope = build_etapa8_envelope(report, rem)
    envelope["report"] = report.to_dict()
    envelope["remediation"] = rem.to_dict()
    return envelope


@router.get("/diagnostics/ml-models", include_in_schema=False)
async def ml_models_status(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Return all ml_models rows ordered by version descending."""
    _enforce_auth(authorization)
    from ..database import AsyncSessionLocal
    from sqlalchemy import text as _text
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(_text("""
            SELECT version, status,
                   precision_score, recall_score, f1_score, roc_auc,
                   win_fast_capture_rate, false_positive_rate,
                   train_samples, val_samples, test_samples,
                   decision_threshold, activated_at, retired_at, notes
            FROM ml_models ORDER BY version DESC
        """))).mappings().all()
    return {
        "models": [
            {
                "version": r["version"],
                "status": r["status"],
                "precision": float(r["precision_score"]) if r["precision_score"] is not None else None,
                "recall": float(r["recall_score"]) if r["recall_score"] is not None else None,
                "f1": float(r["f1_score"]) if r["f1_score"] is not None else None,
                "roc_auc": float(r["roc_auc"]) if r["roc_auc"] is not None else None,
                "capture_rate": float(r["win_fast_capture_rate"]) if r["win_fast_capture_rate"] is not None else None,
                "fpr": float(r["false_positive_rate"]) if r["false_positive_rate"] is not None else None,
                "train_samples": r["train_samples"],
                "val_samples": r["val_samples"],
                "test_samples": r["test_samples"],
                "decision_threshold": float(r["decision_threshold"]) if r["decision_threshold"] is not None else None,
                "activated_at": r["activated_at"].isoformat() if r["activated_at"] else None,
                "retired_at": r["retired_at"].isoformat() if r["retired_at"] else None,
                "notes": r["notes"],
            }
            for r in rows
        ]
    }


@router.get("/diagnostics/ml-label-inversion", include_in_schema=False)
async def ml_label_inversion_diagnostic(
    days: int = 90,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Diagnose whether the XGBoost model has inverted predictions.

    AUC < 0.5 means the model scores LOSSes higher than WINs. This endpoint
    runs the active model against all labeled decisions_log records in-process
    and computes:

    - ``auc_normal``:   roc_auc with current proba vs is_win_fast labels
    - ``auc_inverted``: roc_auc with (1-proba) vs labels  — if this > auc_normal
                        the model is inverted and predictions should be flipped
    - ``mean_pnl_approved``:  mean pnl_pct for records where proba >= threshold
    - ``mean_pnl_rejected``:  mean pnl_pct for records where proba < threshold
    - ``mean_pnl_inv_approved``: same for inverted threshold
    - ``score_pnl_buckets``: L3 score decile vs mean pnl (L3 signal quality)
    - ``ml_predictions_count``: rows in ml_predictions (0 = table not populated)
    - ``label_distribution``: WIN / LOSS counts and base win rate

    Auth: same ADMIN_DIAGNOSTICS_TOKEN as other admin endpoints.
    """
    _enforce_auth(authorization)

    import json as _json
    import numpy as np
    from sklearn.metrics import roc_auc_score

    from ..database import AsyncSessionLocal
    from ..ml.feature_extractor import FEATURE_COLUMNS, build_training_dataframe
    from ..ml.gcs_model_loader import get_model
    from sqlalchemy import text as _text

    out: Dict[str, Any] = {
        "days_lookback": days,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    async with AsyncSessionLocal() as db:
        # ── 1. ml_predictions table status ───────────────────────────────────
        try:
            mp_row = (await db.execute(_text(
                "SELECT COUNT(*) AS n FROM ml_predictions"
            ))).fetchone()
            out["ml_predictions_count"] = int(mp_row.n) if mp_row else 0
        except Exception as exc:
            out["ml_predictions_count"] = f"error: {exc}"

        # ── 2. ml_predictions × decisions_log (if populated) ─────────────────
        if isinstance(out["ml_predictions_count"], int) and out["ml_predictions_count"] > 0:
            try:
                mp_rows = (await db.execute(_text("""
                    SELECT mp.win_fast_probability,
                           mp.threshold_used,
                           dl.pnl_pct,
                           dl.outcome
                    FROM ml_predictions mp
                    JOIN decisions_log dl ON dl.id = mp.decision_id
                    WHERE dl.pnl_pct IS NOT NULL
                      AND dl.outcome IN ('tp', 'sl')
                    ORDER BY mp.created_at DESC
                    LIMIT 2000
                """))).mappings().all()
                out["ml_predictions_labeled"] = len(mp_rows)
                if mp_rows:
                    from ..ml.feature_extractor import _WIN_THRESHOLD
                    probas = np.array([float(r["win_fast_probability"]) for r in mp_rows], dtype="float32")
                    pnls   = np.array([float(r["pnl_pct"]) for r in mp_rows], dtype="float32")
                    labels = (pnls > _WIN_THRESHOLD).astype(int)
                    thresh = float(mp_rows[0]["threshold_used"] or 0.5)
                    approved = probas >= thresh
                    inv_approved = (1 - probas) >= thresh
                    if len(np.unique(labels)) >= 2:
                        out["auc_from_ml_predictions"] = {
                            "auc_normal":   round(float(roc_auc_score(labels, probas)), 4),
                            "auc_inverted": round(float(roc_auc_score(labels, 1 - probas)), 4),
                        }
                    out["pnl_from_ml_predictions"] = {
                        "mean_pnl_approved":     round(float(pnls[approved].mean()), 6) if approved.sum() > 0 else None,
                        "mean_pnl_rejected":     round(float(pnls[~approved].mean()), 6) if (~approved).sum() > 0 else None,
                        "mean_pnl_inv_approved": round(float(pnls[inv_approved].mean()), 6) if inv_approved.sum() > 0 else None,
                        "n_approved": int(approved.sum()),
                        "n_inv_approved": int(inv_approved.sum()),
                    }
            except Exception as exc:
                out["ml_predictions_join_error"] = str(exc)

        # ── 3. In-process model scoring on labeled decisions_log ─────────────
        try:
            records_rows = (await db.execute(_text(f"""
                SELECT DISTINCT ON (symbol, DATE(created_at))
                    id, symbol, created_at, metrics, score,
                    pnl_pct, holding_seconds, outcome, decision
                FROM decisions_log
                WHERE l3_pass = true
                  AND decision = 'ALLOW'
                  AND outcome IN ('tp', 'sl')
                  AND pnl_pct IS NOT NULL
                  AND created_at >= NOW() - INTERVAL '{days} days'
                ORDER BY symbol, DATE(created_at), created_at ASC
            """))).mappings().all()
            records = []
            for r in records_rows:
                rec = dict(r)
                if isinstance(rec.get("metrics"), str):
                    try:
                        rec["metrics"] = _json.loads(rec["metrics"])
                    except Exception:
                        rec["metrics"] = {}
                records.append(rec)
            out["labeled_decisions_found"] = len(records)
        except Exception as exc:
            out["labeled_decisions_error"] = str(exc)
            records = []

        # ── 4. Label distribution ─────────────────────────────────────────────
        if records:
            try:
                from ..ml.feature_extractor import _WIN_THRESHOLD, _MIN_WIN_PNL_PCT, _FEE_ROUND_TRIP_PCT
                pnl_arr = np.array([float(r["pnl_pct"]) for r in records], dtype="float32")
                labels_arr = (pnl_arr > _WIN_THRESHOLD).astype(int)
                out["label_distribution"] = {
                    "win_threshold": round(_WIN_THRESHOLD, 4),
                    "min_win_pnl_pct": _MIN_WIN_PNL_PCT,
                    "fee_round_trip_pct": _FEE_ROUND_TRIP_PCT,
                    "n_total": len(labels_arr),
                    "n_wins": int(labels_arr.sum()),
                    "n_losses": int((labels_arr == 0).sum()),
                    "base_win_rate": round(float(labels_arr.mean()), 4),
                    "mean_pnl": round(float(pnl_arr.mean()), 6),
                    "p25_pnl": round(float(np.percentile(pnl_arr, 25)), 6),
                    "p50_pnl": round(float(np.percentile(pnl_arr, 50)), 6),
                    "p75_pnl": round(float(np.percentile(pnl_arr, 75)), 6),
                }
            except Exception as exc:
                out["label_distribution_error"] = str(exc)

        # ── 5. L3 score decile vs mean PnL (L3 signal quality) ───────────────
        if records:
            try:
                score_arr = np.array(
                    [float(r["score"]) if r.get("score") is not None else float("nan")
                     for r in records],
                    dtype="float32",
                )
                pnl_arr2 = np.array([float(r["pnl_pct"]) for r in records], dtype="float32")
                valid = ~np.isnan(score_arr)
                if valid.sum() >= 20:
                    deciles = np.percentile(score_arr[valid], np.arange(0, 110, 10))
                    buckets = []
                    for i in range(len(deciles) - 1):
                        mask = valid & (score_arr >= deciles[i]) & (score_arr < deciles[i + 1])
                        if mask.sum() == 0:
                            continue
                        buckets.append({
                            "score_min": round(float(deciles[i]), 2),
                            "score_max": round(float(deciles[i + 1]), 2),
                            "n": int(mask.sum()),
                            "mean_pnl": round(float(pnl_arr2[mask].mean()), 6),
                            "win_rate": round(float((pnl_arr2[mask] > _WIN_THRESHOLD).mean()), 4),
                        })
                    out["l3_score_vs_pnl"] = buckets
                else:
                    out["l3_score_vs_pnl"] = "not_enough_scored_records"
            except Exception as exc:
                out["l3_score_vs_pnl_error"] = str(exc)

        # ── 6. In-process AUC (model scored against labeled data) ─────────────
        if records:
            try:
                df = build_training_dataframe(records)
                model = get_model()
                feature_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
                X = df[feature_cols].astype("float32").to_numpy()
                expected = getattr(model, "n_features_in_", None)
                if expected and X.shape[1] > expected:
                    X = X[:, :expected]
                probas = model.predict_proba(X)[:, 1]
                labels_df = df["is_win_fast"].to_numpy(dtype=int)
                pnls_df = df["_pnl_pct"].to_numpy(dtype="float32")

                threshold_row = (await db.execute(_text("""
                    SELECT decision_threshold FROM ml_models
                    WHERE status = 'active' ORDER BY activated_at DESC LIMIT 1
                """))).fetchone()
                threshold = float(threshold_row.decision_threshold) if threshold_row else 0.5

                approved = probas >= threshold
                inv_approved = (1 - probas) >= threshold

                auc_n = float(roc_auc_score(labels_df, probas)) if len(np.unique(labels_df)) >= 2 else None
                auc_i = float(roc_auc_score(labels_df, 1 - probas)) if len(np.unique(labels_df)) >= 2 else None

                out["in_process_scoring"] = {
                    "n_records": len(df),
                    "n_features": X.shape[1],
                    "threshold": threshold,
                    "auc_normal":             round(auc_n, 4) if auc_n is not None else None,
                    "auc_inverted":           round(auc_i, 4) if auc_i is not None else None,
                    "verdict": (
                        "INVERTED — flip prediction" if (auc_i is not None and auc_n is not None and auc_i > auc_n)
                        else ("OK — model is not inverted" if (auc_n is not None and auc_n >= 0.5)
                              else "WEAK — AUC < 0.5 but not clearly inverted")
                    ),
                    "mean_pnl_approved":     round(float(pnls_df[approved].mean()), 6) if approved.sum() > 0 else None,
                    "mean_pnl_rejected":     round(float(pnls_df[~approved].mean()), 6) if (~approved).sum() > 0 else None,
                    "mean_pnl_inv_approved": round(float(pnls_df[inv_approved].mean()), 6) if inv_approved.sum() > 0 else None,
                    "n_approved": int(approved.sum()),
                    "n_inv_approved": int(inv_approved.sum()),
                    "proba_mean": round(float(probas.mean()), 4),
                    "proba_p10":  round(float(np.percentile(probas, 10)), 4),
                    "proba_p50":  round(float(np.percentile(probas, 50)), 4),
                    "proba_p90":  round(float(np.percentile(probas, 90)), 4),
                }
            except Exception as exc:
                out["in_process_scoring_error"] = str(exc)

    return out


@router.get("/quarantine-status", include_in_schema=False)
async def quarantine_status(
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Real-time L3 quarantine metrics.

    Reports how many L3 symbols are currently blocked by ``is_complete()``
    (missing or stale ``REQUIRED_CORE_INDICATORS``: adx, rsi, macd_histogram).

    Auth: same ``ADMIN_DIAGNOSTICS_TOKEN`` as other admin endpoints.

    Returns::

        {
          "checked_at": "2026-05-29T13:00:00Z",
          "stale_threshold_seconds": 1800,
          "l3_total_symbols": 42,
          "quarantined": 7,
          "quarantine_rate_pct": 16.7,
          "by_reason": {
            "rsi_absent": 2,
            "adx_absent": 1,
            "macd_histogram_absent": 0,
            "stale_structural": 4,
            "no_indicator_row": 0
          },
          "oldest_quarantine_age_seconds": 1823,
          "symbols_quarantined": ["AAVE_USDT", ...]
        }
    """
    _enforce_auth(authorization)

    from ..database import AsyncSessionLocal
    from ..utils.indicator_merge import STRUCTURAL_STALE_SECONDS

    checked_at = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        # L3 symbols currently active (not marked down)
        l3_rows = (await db.execute(text("""
            SELECT DISTINCT pwa.symbol
            FROM pipeline_watchlist_assets pwa
            JOIN pipeline_watchlists pw ON pw.id = pwa.watchlist_id
            WHERE pw.level = 'L3'
              AND pwa.level_direction IS DISTINCT FROM 'down'
        """))).fetchall()
        l3_symbols = [r.symbol for r in l3_rows]

        if not l3_symbols:
            return {
                "checked_at": checked_at.isoformat(),
                "stale_threshold_seconds": STRUCTURAL_STALE_SECONDS,
                "l3_total_symbols": 0,
                "quarantined": 0,
                "quarantine_rate_pct": 0.0,
                "by_reason": {
                    "rsi_absent": 0,
                    "adx_absent": 0,
                    "macd_histogram_absent": 0,
                    "stale_structural": 0,
                    "no_indicator_row": 0,
                },
                "oldest_quarantine_age_seconds": None,
                "symbols_quarantined": [],
            }

        # Latest structural indicator row per L3 symbol
        ind_rows = (await db.execute(text("""
            SELECT DISTINCT ON (symbol)
                symbol,
                time,
                EXTRACT(EPOCH FROM (NOW() - time))        AS age_s,
                indicators_json->>'rsi'                   AS rsi,
                indicators_json->>'adx'                   AS adx,
                indicators_json->>'macd_histogram'        AS macd_h
            FROM indicators
            WHERE symbol = ANY(:syms)
              AND scheduler_group = 'structural'
            ORDER BY symbol, time DESC
        """), {"syms": l3_symbols})).fetchall()

        ind_by_sym = {r.symbol: r for r in ind_rows}

        quarantined_syms = []
        by_reason: Dict[str, int] = {
            "rsi_absent": 0,
            "adx_absent": 0,
            "macd_histogram_absent": 0,
            "stale_structural": 0,
            "no_indicator_row": 0,
        }
        oldest_age: Optional[float] = None

        for sym in l3_symbols:
            row = ind_by_sym.get(sym)
            if row is None:
                quarantined_syms.append(sym)
                by_reason["no_indicator_row"] += 1
                continue

            age_s = float(row.age_s) if row.age_s is not None else float("inf")
            is_stale = age_s > STRUCTURAL_STALE_SECONDS
            rsi_absent = row.rsi is None
            adx_absent = row.adx is None
            macd_absent = row.macd_h is None

            if is_stale or rsi_absent or adx_absent or macd_absent:
                quarantined_syms.append(sym)
                if is_stale:
                    by_reason["stale_structural"] += 1
                if rsi_absent:
                    by_reason["rsi_absent"] += 1
                if adx_absent:
                    by_reason["adx_absent"] += 1
                if macd_absent:
                    by_reason["macd_histogram_absent"] += 1
                if oldest_age is None or age_s > oldest_age:
                    oldest_age = age_s

    total = len(l3_symbols)
    n_quarantined = len(quarantined_syms)
    return {
        "checked_at": checked_at.isoformat(),
        "stale_threshold_seconds": STRUCTURAL_STALE_SECONDS,
        "l3_total_symbols": total,
        "quarantined": n_quarantined,
        "quarantine_rate_pct": round(100.0 * n_quarantined / total, 1) if total else 0.0,
        "by_reason": by_reason,
        "oldest_quarantine_age_seconds": round(oldest_age, 0) if oldest_age is not None else None,
        "symbols_quarantined": sorted(quarantined_syms),
    }
