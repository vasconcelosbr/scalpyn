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

from fastapi import APIRouter, Header, HTTPException, status
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
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(text("""
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
        history = [
            {
                "time": r.time.isoformat() if r.time else None,
                "age_seconds": _age_seconds(r.time),
                "timeframe": r.timeframe,
                "scheduler_group": r.scheduler_group,
                "indicator_keys": list(r.keys or []),
                "has_taker_ratio": "taker_ratio" in (r.keys or []),
                "has_volume_spike": "volume_spike" in (r.keys or []),
                "has_volume_delta": "volume_delta" in (r.keys or []),
                "has_spread_pct": "spread_pct" in (r.keys or []),
            }
            for r in rows
        ]
        return {"ok": True, "rows": history}
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
        from ..services.order_flow_service import get_order_flow_data
        of = await get_order_flow_data(symbol, window_seconds=300)
        return {
            "ok": True,
            "taker_ratio": of.get("taker_ratio") if of else None,
            "buy_pressure": of.get("buy_pressure") if of else None,
            "volume_delta": of.get("volume_delta") if of else None,
            "taker_buy_volume": of.get("taker_buy_volume") if of else None,
            "taker_sell_volume": of.get("taker_sell_volume") if of else None,
            "source": of.get("taker_source") if of else None,
            "window": of.get("taker_window") if of else None,
        }
    except Exception as exc:
        logger.warning("[admin-diag] live_order_flow failed for %s: %s", symbol, exc)
        return _err(exc)


async def _probe_ws_leader_status() -> Dict[str, Any]:
    try:
        from ..services.gate_ws_leader import LEADER_KEY
        from ..services.redis_client import get_async_redis

        redis = await get_async_redis()
        if redis is None:
            return {
                "ok": True,
                "redis_available": False,
            }
        holder = await redis.get(LEADER_KEY)
        ttl = await redis.ttl(LEADER_KEY)
        return {
            "ok": True,
            "redis_available": True,
            "leader_holder": (
                holder.decode("utf-8", errors="replace")
                if isinstance(holder, (bytes, bytearray))
                else holder
            ),
            "leader_ttl_seconds": int(ttl) if ttl is not None and ttl >= 0 else None,
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
