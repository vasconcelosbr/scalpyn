"""Pump Monitor — I/O: config, flow buckets, 30 s cycle, REALTIME sync, API reads.

OBSERVATION ONLY. The cycle never touches profiles, L3, the execution
engine or bots. The only pool it writes is one whose operator enabled
``pump_monitor_sync_enabled`` — and such a pool is ``observation_only``
(filtered out of every execution universe, see
``pool_service.EXECUTION_POOL_PREDICATE_SQL``).
"""
from __future__ import annotations

import asyncio
import json
import math
from copy import deepcopy
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text

from . import flow_metrics as fm
from . import pump_monitor_engine as eng
from . import pump_research as research
from . import pump_score_v1 as v1
from .pump_universe import select_universe

logger = logging.getLogger(__name__)

CONFIG_TYPE = "pump_monitor"
_LATEST_KEY = "pump_monitor:latest:{user_id}"
_STATE_KEY = "pump_monitor:state:{user_id}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def _float(value):
    return float(value) if value is not None else None


# ── Config (versioned, audited via ConfigService) ────────────────────────────

async def load_stored_config(db, user_id) -> Optional[Dict[str, Any]]:
    row = (await db.execute(text("""
        SELECT config_json FROM config_profiles
         WHERE config_type = :t AND user_id = :u AND pool_id IS NULL
         ORDER BY updated_at DESC NULLS LAST LIMIT 1
    """), {"t": CONFIG_TYPE, "u": str(user_id)})).scalar()
    return dict(row) if row else None


async def get_config(db, user_id) -> Dict[str, Any]:
    return eng.effective_config(await load_stored_config(db, user_id))


async def put_config(db, user_id, requested: Dict[str, Any]) -> Dict[str, Any]:
    from .config_service import config_service
    previous = await load_stored_config(db, user_id)
    new = eng.next_config_version(previous, requested, changed_by=str(user_id),
                                  now_iso=datetime.now(timezone.utc).isoformat())
    await config_service.update_config(
        db, CONFIG_TYPE, user_id, new, changed_by=user_id,
        change_description=f"pump_monitor v{new['_meta']['version']} hash={new['_meta']['config_hash']}",
    )
    return eng.effective_config(new)


async def _enabled_configs(db) -> List[tuple]:
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (user_id) user_id, config_json FROM config_profiles
         WHERE config_type = :t AND pool_id IS NULL AND is_active IS NOT FALSE
         ORDER BY user_id, updated_at DESC NULLS LAST
    """), {"t": CONFIG_TYPE})).all()
    return [(r.user_id, eng.effective_config(r.config_json)) for r in rows]


async def _universe(db, user_id, pool_id) -> List[str]:
    rows = (await db.execute(text("""
        SELECT DISTINCT pc.symbol FROM pool_coins pc JOIN pools p ON p.id = pc.pool_id
         WHERE p.id = CAST(:pid AS uuid) AND p.user_id = :uid AND p.is_active
           AND pc.is_active AND pc.market_type = 'spot'
         ORDER BY pc.symbol
    """), {"pid": str(pool_id), "uid": str(user_id)})).scalars().all()
    return list(rows)


# ── Flow buckets ─────────────────────────────────────────────────────────────

_UPSERT_BUCKET = text("""
    INSERT INTO flow_buckets_1m (symbol, bucket_start, bucket_seconds, buy_base, sell_base,
        buy_quote, sell_quote, trade_count, first_trade_id, last_trade_id, open_price,
        high_price, low_price, close_price, partial, gap_reason, source, computed_at)
    VALUES (:symbol, :bucket_start, 60, :buy_base, :sell_base, :buy_quote, :sell_quote,
        :trade_count, :first_trade_id, :last_trade_id, :open_price, :high_price, :low_price,
        :close_price, :partial, :gap_reason, :source, now())
    ON CONFLICT (symbol, bucket_start) DO UPDATE SET
        buy_base = EXCLUDED.buy_base, sell_base = EXCLUDED.sell_base,
        buy_quote = EXCLUDED.buy_quote, sell_quote = EXCLUDED.sell_quote,
        trade_count = EXCLUDED.trade_count, first_trade_id = EXCLUDED.first_trade_id,
        last_trade_id = EXCLUDED.last_trade_id, open_price = EXCLUDED.open_price,
        high_price = EXCLUDED.high_price, low_price = EXCLUDED.low_price,
        close_price = EXCLUDED.close_price, partial = EXCLUDED.partial,
        gap_reason = EXCLUDED.gap_reason, source = EXCLUDED.source, computed_at = now()
    -- a complete bucket is never downgraded by a later, less covered read
    WHERE flow_buckets_1m.partial OR NOT EXCLUDED.partial
""")


def _bucket_params(symbol: str, row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "bucket_start": datetime.fromtimestamp(row["bucket_start_ms"] / 1000.0, tz=timezone.utc),
        **{k: row.get(k) for k in ("buy_base", "sell_base", "buy_quote", "sell_quote", "trade_count",
                                   "first_trade_id", "last_trade_id", "open_price", "high_price",
                                   "low_price", "close_price", "partial", "gap_reason", "source")},
    }


async def _load_buckets(db, symbols: List[str], since_ms: int) -> Dict[str, Dict[int, Dict[str, Any]]]:
    rows = (await db.execute(text("""
        SELECT * FROM flow_buckets_1m
         WHERE symbol = ANY(CAST(:s AS text[])) AND bucket_start >= :since
    """), {"s": symbols, "since": datetime.fromtimestamp(since_ms / 1000.0, tz=timezone.utc)})).mappings().all()
    out: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for r in rows:
        start = int(r["bucket_start"].timestamp() * 1000)
        out.setdefault(r["symbol"], {})[start] = {
            "bucket_start_ms": start,
            **{k: _float(r[k]) for k in ("buy_base", "sell_base", "buy_quote", "sell_quote",
                                         "open_price", "high_price", "low_price", "close_price")},
            "trade_count": r["trade_count"], "partial": bool(r["partial"]),
            "gap_reason": r["gap_reason"], "source": r["source"],
        }
    return out


# ── Other inputs ─────────────────────────────────────────────────────────────

async def _load_candles(db, symbols: List[str], timeframe: str) -> Dict[str, Dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (symbol) symbol, time, open, high, low, close
          FROM ohlcv
         WHERE symbol = ANY(CAST(:s AS text[])) AND timeframe = :tf
           AND market_type = 'spot' AND is_closed IS TRUE
           AND time >= now() - interval '1 day'
         ORDER BY symbol, time DESC
    """), {"s": symbols, "tf": timeframe})).mappings().all()
    return {r["symbol"]: {"time": r["time"].isoformat(), "open": _float(r["open"]), "high": _float(r["high"]),
                          "low": _float(r["low"]), "close": _float(r["close"])} for r in rows}


_TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


async def _load_candle_series(db, symbols: List[str], timeframe: str, count: int) -> Dict[str, List[Dict[str, Any]]]:
    """Last ``count`` CLOSED spot candles per symbol, ascending. One row per timestamp:
    Gate is preferred when another exchange wrote the same candle (fallback rows keep
    their ``exchange`` as provenance)."""
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (symbol, time) symbol, time, exchange, open, high, low, close, volume
          FROM ohlcv
         WHERE symbol = ANY(CAST(:s AS text[])) AND timeframe = :tf
           AND market_type = 'spot' AND is_closed IS TRUE
           AND time >= :since
         ORDER BY symbol, time, CASE WHEN exchange ILIKE 'gate%' THEN 0 ELSE 1 END
    """), {"s": symbols, "tf": timeframe,
           # asyncpg binds timestamptz from datetime only (no text/interval casting)
           "since": datetime.now(timezone.utc) - timedelta(seconds=_TF_SECONDS[timeframe] * (int(count) + 2))})).mappings().all()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append({
            "time_ms": int(r["time"].timestamp() * 1000), "exchange": r["exchange"],
            **{k: _float(r[k]) for k in ("open", "high", "low", "close", "volume")}})
    return {sym: series[-int(count):] for sym, series in out.items()}


async def _load_alpha(db, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (symbol) symbol, time, score, liquidity_score, momentum_score
          FROM alpha_scores
         WHERE symbol = ANY(CAST(:s AS text[])) AND time >= now() - interval '1 hour'
         ORDER BY symbol, time DESC
    """), {"s": symbols})).mappings().all()
    now = datetime.now(timezone.utc)
    return {r["symbol"]: {"values": {k: _float(r[k]) for k in ("score", "liquidity_score", "momentum_score")},
                          "age_seconds": round((now - r["time"]).total_seconds(), 1)} for r in rows}


async def _collect_symbol(symbol: str, minutes: List[int], config: Dict[str, Any]) -> Dict[str, Any]:
    from .order_flow_service import read_raw_trades
    from .market_data_service import market_data_service
    raw = await read_raw_trades(symbol, since_ms=minutes[0])
    rows = fm.materialize_buckets(
        fm.bucket_trades(raw["trades"]), minutes,
        covered_from_ms=raw["covered_from_ms"], source=raw["source"],
        gap_reason=raw["gap_reason"], alive_slots=raw.get("alive_slots"),
        gap_windows=raw.get("gap_windows") or (),
    )
    book = await market_data_service.fetch_raw_orderbook(symbol, int(config["book"]["limit"]))
    if book:
        observed = book.get("_observed_at")
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(observed)).total_seconds()
        except (TypeError, ValueError):
            age = None
        book = {"bids": book.get("bids"), "asks": book.get("asks"), "observed_at": observed,
                "age_seconds": round(age, 1) if age is not None else None}
    return {"buckets": rows, "book": book, "raw_price_input": raw}


# ── Capital-flow regime (v1.4) ───────────────────────────────────────────────

def capital_minutes(buckets_by_symbol: Dict[str, Dict[int, Dict[str, Any]]], symbols: List[str],
                    last_minute_ms: int, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-minute USDT taker flow of the whole universe (oldest first).

    Only USDT-quoted, non-excluded symbols; a symbol-minute counts only when its
    bucket is complete (same usability rule as every flow metric)."""
    cf = spec.get("capital_flow") or {}
    suffix = str(cf.get("quote_suffix") or "_USDT")
    excluded = set(cf.get("exclude_symbols") or [])
    universe = [s for s in symbols if s.endswith(suffix) and s not in excluded]
    count = int(cf.get("window_minutes") or 0)
    out = []
    for i in range(count - 1, -1, -1):
        minute = last_minute_ms - i * 60_000
        buy = sell = 0.0
        complete = 0
        for symbol in universe:
            bucket = (buckets_by_symbol.get(symbol) or {}).get(minute)
            if bucket is None or bucket.get("partial"):
                continue
            complete += 1
            buy += float(bucket.get("buy_quote") or 0.0)
            sell += float(bucket.get("sell_quote") or 0.0)
        out.append({"minute_ms": minute, "buy_usdt": buy, "sell_usdt": sell,
                    "symbols": len(universe), "complete_symbols": complete})
    return out


_UPSERT_CAPITAL = text("""
    INSERT INTO pump_capital_flow_1m (user_id, minute, buy_usdt, sell_usdt, net_usdt, symbols,
        complete_symbols, window_minutes, window_net_usdt, window_ratio, z, state, method,
        config_version, computed_at)
    VALUES (CAST(:user_id AS uuid), :minute, :buy_usdt, :sell_usdt, :net_usdt, :symbols,
        :complete_symbols, :window_minutes, :window_net_usdt, :window_ratio, :z, :state, :method,
        :config_version, now())
    ON CONFLICT (user_id, minute) DO UPDATE SET
        buy_usdt = EXCLUDED.buy_usdt, sell_usdt = EXCLUDED.sell_usdt, net_usdt = EXCLUDED.net_usdt,
        symbols = EXCLUDED.symbols, complete_symbols = EXCLUDED.complete_symbols,
        window_minutes = EXCLUDED.window_minutes, window_net_usdt = EXCLUDED.window_net_usdt,
        window_ratio = EXCLUDED.window_ratio, z = EXCLUDED.z, state = EXCLUDED.state,
        method = EXCLUDED.method, config_version = EXCLUDED.config_version, computed_at = now()
""")


def capital_row(user_id, minute: Dict[str, Any], capital: Dict[str, Any], config_version: int) -> Dict[str, Any]:
    return {
        "user_id": str(user_id),
        "minute": datetime.fromtimestamp(minute["minute_ms"] / 1000.0, tz=timezone.utc),
        "buy_usdt": round(minute["buy_usdt"], 2), "sell_usdt": round(minute["sell_usdt"], 2),
        "net_usdt": round(minute["buy_usdt"] - minute["sell_usdt"], 2),
        "symbols": minute["symbols"], "complete_symbols": minute["complete_symbols"],
        "window_minutes": capital.get("window_minutes"), "window_net_usdt": capital.get("net_usdt"),
        "window_ratio": capital.get("ratio"), "z": capital.get("z"), "state": capital.get("state"),
        "method": capital.get("method"), "config_version": config_version,
    }


async def capital_flow_history(db, user_id, *, days: int, top: int, tz_offset_minutes: int) -> Dict[str, Any]:
    """Hourly capital tide history: strongest inflow/outflow hours and the
    hour-of-day profile. Hours are bucketed in the caller's local time
    (``tz_offset_minutes``, e.g. -180 for BRT) so the profile reads naturally."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    offset = timedelta(minutes=tz_offset_minutes)
    rows = (await db.execute(text("""
        SELECT date_trunc('hour', (minute AT TIME ZONE 'UTC') + CAST(:off AS interval)) AS local_hour,
               SUM(buy_usdt) AS buy, SUM(sell_usdt) AS sell, SUM(net_usdt) AS net,
               COUNT(*) AS minutes, AVG(complete_symbols::float / NULLIF(symbols, 0)) AS coverage
          FROM pump_capital_flow_1m
         WHERE user_id = CAST(:u AS uuid) AND minute >= :since
         GROUP BY 1 ORDER BY 1
    """), {"u": str(user_id), "since": since, "off": offset})).mappings().all()
    hours = []
    for r in rows:
        buy, sell = _float(r["buy"]) or 0.0, _float(r["sell"]) or 0.0
        gross = buy + sell
        hours.append({"hour": r["local_hour"].isoformat(timespec="minutes"),
                      "buy_usdt": round(buy, 2), "sell_usdt": round(sell, 2),
                      "net_usdt": round(buy - sell, 2), "ratio": round((buy - sell) / gross, 5) if gross else None,
                      "minutes": int(r["minutes"]), "coverage": round(_float(r["coverage"]) or 0.0, 4)})
    complete = [h for h in hours if h["minutes"] >= 30 and h["ratio"] is not None]
    by_ratio = sorted(complete, key=lambda h: h["ratio"])
    profile: Dict[int, List[float]] = {}
    for h in complete:
        profile.setdefault(int(h["hour"][11:13]), []).append(h["ratio"])
    hour_of_day = [{"hour": hr, "avg_ratio": round(sum(v) / len(v), 5), "days": len(v)}
                   for hr, v in sorted(profile.items())]
    return {"days": days, "tz_offset_minutes": tz_offset_minutes, "hours": hours,
            "top_inflow": list(reversed(by_ratio[-top:])), "top_outflow": by_ratio[:top],
            "hour_of_day": hour_of_day,
            "note": "ratio = (compra − venda) / (compra + venda) a mercado em USDT do universo; horas com >= 30 min de dado"}


# ── Spot × perpetual context (v1.5) ──────────────────────────────────────────

GATE_FUTURES_URL = "https://api.gateio.ws/api/v4/futures/usdt"
_PERPS_KEY = "pump_monitor:perp_contracts"
_PERP_STATS_KEY = "pump_monitor:perp_stats:{symbol}"


async def _perp_contracts(redis, client) -> Optional[set]:
    """Active USDT perpetual names, cached for a day (one public call)."""
    cached = await _read_json(redis, _PERPS_KEY)
    if cached and isinstance(cached.get("names"), list):
        return set(cached["names"])
    resp = await client.get(f"{GATE_FUTURES_URL}/contracts")
    resp.raise_for_status()
    names = sorted(c["name"] for c in resp.json() if c.get("name") and not c.get("in_delisting"))
    if redis is not None and names:
        try:
            await redis.set(_PERPS_KEY, json.dumps({"names": names}), ex=86_400)
        except Exception as exc:
            logger.warning("[PUMP-PERP] contract cache write failed: %s", exc)
    return set(names)


async def load_derivatives(symbols: List[str], spec: Dict[str, Any], now_ms: int,
                           concurrency: int) -> Dict[str, Optional[List[Dict[str, Any]]]]:
    """``{symbol: contract_stats rows}``: ``[]`` = no perpetual, ``None`` = fetch failed.

    One Gate call per perpetual per closed interval; between interval closes the
    Redis copy is reused, so the 30 s cycle does not multiply requests."""
    import httpx
    d = spec.get("derivatives") or {}
    if not d.get("enabled") or not symbols:
        return {}
    interval = str(d["interval"])
    slot_ms = (now_ms // v1._TF_MS[interval]) * v1._TF_MS[interval]
    limit = int(d["window_intervals"]) + 1
    redis = await _redis()
    out: Dict[str, Optional[List[Dict[str, Any]]]] = {}
    async with httpx.AsyncClient(timeout=10) as client:
        perps = await _perp_contracts(redis, client)
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def _one(symbol):
            if symbol not in perps:
                out[symbol] = []
                return
            key = _PERP_STATS_KEY.format(symbol=symbol)
            cached = await _read_json(redis, key)
            if cached and cached.get("slot_ms") == slot_ms:
                out[symbol] = cached.get("rows") or []
                return
            async with semaphore:
                try:
                    resp = await client.get(f"{GATE_FUTURES_URL}/contract_stats",
                                            params={"contract": symbol, "interval": interval, "limit": limit})
                    resp.raise_for_status()
                    keep = ("time", "long_taker_size", "short_taker_size", "open_interest_usd",
                            "short_liq_usd", "long_liq_usd", "last_funding_rate", "lsr_taker", "mark_price")
                    rows = [{k: r.get(k) for k in keep} for r in resp.json()]
                except Exception as exc:
                    logger.warning("[PUMP-PERP] stats failed symbol=%s reason=%s", symbol, type(exc).__name__)
                    out[symbol] = None
                    return
            out[symbol] = rows
            if redis is not None:
                try:
                    await redis.set(key, json.dumps({"slot_ms": slot_ms, "rows": rows}),
                                    ex=v1._TF_MS[interval] // 1000 * 3)
                except Exception:
                    pass

        await asyncio.gather(*(_one(s) for s in symbols))
    return out


# ── Redis state ──────────────────────────────────────────────────────────────

async def _redis():
    from .redis_client import get_async_redis
    try:
        return await get_async_redis()
    except Exception as exc:
        logger.warning("[PUMP-MONITOR] redis unavailable: %s", exc)
        return None


async def _read_json(redis, key: str) -> Optional[Dict[str, Any]]:
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
    except Exception as exc:
        logger.warning("[PUMP-MONITOR] redis read failed key=%s: %s", key, exc)
        return None
    return json.loads(raw) if raw else None


# ── Cycle ────────────────────────────────────────────────────────────────────

async def run_cycle() -> Dict[str, Any]:
    from ..database import run_db_task
    configs = await run_db_task(_enabled_configs, celery=True)
    results = {}
    for user_id, config in configs:
        if not config.get("enabled") or not config.get("universe_pool_id"):
            results[str(user_id)] = "skipped:not_configured"
            continue
        try:
            results[str(user_id)] = await _cycle_for_user(user_id, config)
        except Exception:
            logger.exception("[PUMP-MONITOR] cycle failed user=%s", user_id)
            results[str(user_id)] = "failed"
    return results


async def _cycle_for_user(user_id, config: Dict[str, Any]) -> Dict[str, Any]:
    from ..database import run_db_task
    from .indicators_provider import get_merged_indicators

    started = time.monotonic()
    now_ms = _now_ms()
    flow = config["flow"]
    last_minute = ((now_ms - int(flow["settle_seconds"]) * 1000) // 60_000) * 60_000 - 60_000
    lookback_minutes = max(1, int(flow["bucket_lookback_seconds"]) // 60)
    minutes = [last_minute - i * 60_000 for i in range(lookback_minutes - 1, -1, -1)]
    pool_id = config["universe_pool_id"]

    candidates = await run_db_task(lambda db: _universe(db, user_id, pool_id), celery=True)
    minute = datetime.fromtimestamp(last_minute / 1000, tz=timezone.utc)
    symbols, drain_symbols, market_caps = await run_db_task(
        lambda db: select_universe(db, candidates, config, minute), celery=True)
    try:
        from .pump_opportunity_service import pending_support_symbols
        v2_support = await run_db_task(lambda db: pending_support_symbols(db,user_id,symbols,minute),celery=True)
        drain_symbols = sorted(set(drain_symbols) | set(v2_support))
    except Exception as exc:
        logger.warning("[PUMP-OPPORTUNITY] support lookup unavailable reason=%s",type(exc).__name__)
    collection_symbols = sorted(set(symbols) | set(drain_symbols))
    # Even an empty universe publishes a fresh envelope rather than stale ranks.

    semaphore = asyncio.Semaphore(int(config["concurrency"]))
    timeout = float(config["symbol_timeout_seconds"])
    failures: Dict[str, str] = {}

    async def _one(symbol):
        async with semaphore:
            try:
                return symbol, await asyncio.wait_for(_collect_symbol(symbol, minutes, config), timeout)
            except Exception as exc:  # one symbol never breaks the cycle
                failures[symbol] = type(exc).__name__
                return symbol, None

    collected = dict(await asyncio.gather(*(_one(s) for s in collection_symbols)))

    async def _persist(db):
        params = [_bucket_params(sym, row) for sym, data in collected.items() if data
                  for row in data["buckets"]]
        if params:
            await db.execute(_UPSERT_BUCKET, params)
    await run_db_task(_persist, celery=True)

    window_minutes = max(int(config["cvd"]["window_minutes"]), int(flow["progress_window_minutes"]),
                         int(flow["persistence_buckets"]), int(config["price"]["breakout_max_age_minutes"]))
    since = last_minute - (window_minutes + 1) * 60_000

    v1_spec = config["score_v1"]
    v1_ref = v1_spec["regime"]["reference_symbol"]

    async def _load(db):
        return (await _load_buckets(db, symbols, since),
                await get_merged_indicators(db, symbols),
                await _load_candles(db, symbols, config["price"]["wick_timeframe"]),
                await _load_alpha(db, symbols))
    buckets_by_symbol, merged, candles, alpha = await run_db_task(_load, celery=True)

    # v1 input in its own transaction: a v1 read failure must never stop the v0 cycle.
    try:
        series = await run_db_task(lambda db: _load_candle_series(
            db, sorted(set(symbols) | {v1_ref}), v1_spec["structure"]["timeframe"],
            int(v1_spec["structure"]["lookback_candles"])), celery=True)
    except Exception as exc:
        logger.warning("[PUMP-SCORE-V1] candle series unavailable user=%s reason=%s", user_id, type(exc).__name__)
        series = {}

    redis = await _redis()
    state_key = _STATE_KEY.format(user_id=user_id)
    state = await _read_json(redis, state_key) or {}
    breakout_state = state.get("breakout") or {}
    alert_state = state.get("alerts") or {}
    cycle_index = int(state.get("cycle_index") or 0) + 1
    level_key = config["price"]["breakout_level_key"]

    rows: List[Dict[str, Any]] = []
    opportunity_source_meta = {}
    new_alert_rows: List[Dict[str, Any]] = []
    # Research dataset: one row per asset per minute, built from what this cycle computed.
    research_due = research.is_research_minute(last_minute, state.get("research_last_minute"), config["research"])
    support_due = (config["research"].get("enabled") and
                   state.get("research_support_last_minute") != last_minute)
    research_rows: List[Dict[str, Any]] = []
    research_keys: Dict[str, tuple] = {}
    research_inputs: Dict[str, tuple] = {}
    for symbol in symbols:
        m = merged.get(symbol)
        snapshot = dict(m.as_flat_dict()) if m else {}
        meta = dict(m.meta) if m else {}
        opportunity_source_meta[symbol] = _json_safe(meta)
        a = alpha.get(symbol)
        if a:
            snapshot.update(a["values"])
            for k in a["values"]:
                meta[k] = {"source": "alpha_scores", "age_seconds": a["age_seconds"]}
        buckets = buckets_by_symbol.get(symbol, {})
        last = buckets.get(last_minute)
        live_price = last.get("close_price") if last and not last.get("partial") else None
        b_state = eng.advance_breakout(
            breakout_state.get(symbol), price=live_price if live_price is not None else snapshot.get("price"),
            level=snapshot.get(level_key), minute_ms=last_minute, config=config)
        breakout_state[symbol] = b_state
        data = collected.get(symbol) or {}
        row = eng.build_row(
            symbol, snapshot=snapshot, snapshot_meta=_json_safe(meta), buckets=buckets,
            book=data.get("book"), candle=candles.get(symbol), breakout_state=b_state,
            last_minute_ms=last_minute, now_ms=now_ms, config=config)
        row["indicators"]["market_cap_usd"] = {"value": market_caps.get(symbol),
                                                "source": "market_metadata", "reason": None}
        row["last_closed_minute"] = _iso(last_minute)
        row["opportunity_source_values"] = {k: snapshot.get(k) for k in
            ("ema9", "ema21", "ema50", "ema200", "atr", "vwap", "recent_high_1h_level")}
        if symbol in failures:
            row["collection_error"] = failures[symbol]
        for alert in eng.new_alerts(alert_state.get(symbol) or [], row["alerts_active"]):
            new_alert_rows.append({"symbol": symbol, "alert_type": alert["type"],
                                   "inputs": json.dumps(_json_safe({**alert["inputs"], "cycle_at": _iso(now_ms)})),
                                   "triggered_at": datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)})
        alert_state[symbol] = [a["type"] for a in row["alerts_active"]]
        research_inputs[symbol] = (last, data.get("book"))
        rows.append(_json_safe(row))

    # Pump Score v1 runs in parallel with v0 on the same rows; ``engines.active``
    # only chooses which one is displayed and drives the REALTIME sync.
    engine = eng.active_engine(config)
    v1_state = state.get("score_v1") or {}
    v1_out = {"regime": None, "results": {}}
    cap_minutes: List[Dict[str, Any]] = []
    try:
        cap_minutes = capital_minutes(buckets_by_symbol, symbols, last_minute, v1_spec)
    except Exception:
        logger.exception("[PUMP-CAPITAL] aggregation failed user=%s", user_id)
    derivatives: Optional[Dict[str, Any]] = None
    try:
        derivatives = await load_derivatives(symbols, v1_spec, now_ms, int(config["concurrency"]))
    except Exception as exc:  # perp context is optional: the cycle never depends on it
        logger.warning("[PUMP-PERP] unavailable user=%s reason=%s", user_id, type(exc).__name__)
    history_cfg = config.get("flow_history") or {}
    if history_cfg.get("enabled"):
        # v1.16: 5-minute flow/perp history for future features; never breaks the cycle.
        try:
            from . import pump_flow_history
            interval = str((v1_spec.get("derivatives") or {}).get("interval") or "5m")
            await run_db_task(lambda db: pump_flow_history.write(db, symbols, derivatives, now_ms, history_cfg,
                                                                 interval, rows, last_minute_ms=last_minute),
                              celery=True)
        except Exception as exc:
            logger.warning("[PUMP-FLOW-HISTORY] write skipped user=%s reason=%s", user_id, type(exc).__name__)
    structures: Dict[str, Any] = {}
    try:
        structures = {sym: v1.structure_metrics(series.get(sym) or [], v1_spec, now_ms)
                      for sym in set(symbols) | {v1_ref}}
    except Exception:
        logger.exception("[PUMP-SCORE-V1] structure metrics failed user=%s", user_id)
    ml_payload: Dict[str, Any] = {"active": False, "reason": "not_loaded", "probabilities": {}}
    try:
        from . import pump_ml_inference
        loaded = await run_db_task(lambda db: pump_ml_inference.load_model(db, user_id, v1_spec), celery=True)
        ml_rows = rows
        predictor = loaded.get("predictor")
        if loaded.get("active") and predictor is not None and predictor.uses_context:
            # ML-independent v1 pre-pass on a throwaway state copy: the model sees the
            # same context cells (v1 structure, perp, regime, capital) the observations
            # store, without stepping the real v1 state twice.
            pre = v1.evaluate_universe(rows, structures, structures.get(v1_ref), deepcopy(v1_state),
                                       minute_ms=last_minute, spec=v1_spec, capital_minutes=cap_minutes,
                                       derivatives=derivatives, now_ms=now_ms, ml=None)
            ctx = context_cells(pre, v1_spec)
            ml_rows = [{**r, "indicators": {**(r.get("indicators") or {}), **ctx.get(r["symbol"], {})}} for r in rows]
        cparams = pump_ml_inference.candle_params(predictor) if loaded.get("active") else None
        if cparams:
            # Candle family: same build_frame as training, params frozen in the manifest,
            # cached per closed candle.
            from . import pump_ml_candles
            cstep = int(cparams["step_seconds"])
            cslot = (now_ms // 1000 // cstep) * cstep
            ckey = f"pump_monitor:ml_candlectx:{user_id}:{cslot}"
            redis_c = await _redis()
            ccells = await _read_json(redis_c, ckey)
            if ccells is None:
                ccells = await run_db_task(lambda db: pump_ml_candles.candle_context(
                    db, [r["symbol"] for r in rows], cparams, cslot), celery=True)
                if redis_c is not None and ccells:   # {} = flow snapshot pending: retry next cycle
                    try:
                        await redis_c.set(ckey, json.dumps(_json_safe(ccells)), ex=2 * cstep)
                    except Exception:
                        pass
            ml_rows = [{**r, "indicators": {**(r.get("indicators") or {}), **(ccells.get(r["symbol"]) or {})}}
                       for r in ml_rows]
        params = pump_ml_inference.relative_params(predictor) if loaded.get("active") else None
        if params:
            # Derived relative features (previous-window beta residual, 24h beta), same
            # function and frozen parameters as training; cached per closed candle.
            step = pump_ml_inference_step(params)
            slot = (now_ms // 1000 // step) * step
            cache_key = f"pump_monitor:ml_relctx:{user_id}:{slot}"
            redis = await _redis()
            rel = await _read_json(redis, cache_key)
            if rel is None:
                rel = await run_db_task(lambda db: pump_ml_inference.relative_context(
                    db, [r["symbol"] for r in rows], params, slot), celery=True)
                if redis is not None:
                    try:
                        await redis.set(cache_key, json.dumps(_json_safe(rel)), ex=2 * step)
                    except Exception:
                        pass
            ml_rows = [{**r, "indicators": {**(r.get("indicators") or {}), **(rel.get(r["symbol"]) or {})}}
                       for r in ml_rows]
        ml_payload = pump_ml_inference.predict_rows(loaded, ml_rows, v1_spec)
        live_log = (v1_spec.get("ml") or {}).get("live_log") or {}
        if cparams and ml_payload.get("active") and live_log.get("enabled"):
            # v1.14: one row per closed candle (decision = close of the candle the features
            # used); duplicates within the slot are ignored. Never breaks the cycle.
            from . import pump_ml_live
            batch = pump_ml_live.prediction_rows(
                loaded.get("model") or {}, pump_ml_inference.CANDLE_OBJECTIVE,
                int(v1_spec["ml"]["horizon_minutes"]), cslot, ml_payload.get("probabilities") or {},
                applied=bool((v1_spec.get("ml") or {}).get("enabled")))
            if batch:
                try:
                    await run_db_task(lambda db: db.execute(text(pump_ml_live.INSERT_SQL), {
                        "u": str(user_id), "batch": json.dumps(batch, allow_nan=False)}), celery=True)
                except Exception as exc:
                    logger.warning("[PUMP-ML] live log skipped user=%s reason=%s", user_id, type(exc).__name__)
    except Exception as exc:  # the ML is optional: no model → zero effect, never a broken cycle
        logger.warning("[PUMP-ML] inference unavailable user=%s reason=%s", user_id, type(exc).__name__)
    try:
        v1_out = v1.evaluate_universe(rows, structures, structures.get(v1_ref), v1_state,
                                      minute_ms=last_minute, spec=v1_spec, capital_minutes=cap_minutes,
                                      derivatives=derivatives, now_ms=now_ms, ml=ml_payload)
    except Exception:
        logger.exception("[PUMP-SCORE-V1] evaluation failed user=%s", user_id)
    capital = (v1_out.get("regime") or {}).get("capital")
    apply_engines(rows, v1_out, engine, v1_spec)

    for row in rows:
        if not research_due:
            break
        try:
            last, book = research_inputs.get(row["symbol"]) or (None, None)
            rec, value_keys, contribution_keys = research.research_row(
                row, minute_ms=last_minute, bucket=last, book=book,
                cycle_at_ms=now_ms, config_meta=config["_meta"])
            research_rows.append(rec)
            research_keys[rec["value_keys_hash"]] = (value_keys, contribution_keys)
        except Exception as exc:
            logger.warning("[PUMP-RESEARCH] row build failed symbol=%s: %s", row["symbol"], type(exc).__name__)

    # Persist price-only support separately; excluded symbols never build a score,
    # enter the public envelope/ranking/REALTIME sync, or become a label target.
    support_symbols_written = set()
    if support_due:
        for symbol in drain_symbols:
            data = collected.get(symbol) or {}
            bucket = next((b for b in data.get("buckets", [])
                           if b.get("bucket_start_ms") == last_minute), None)
            if bucket is None:
                continue
            rec, vk, ck = research.price_support_row(
                symbol, minute_ms=last_minute, bucket=bucket, cycle_at_ms=now_ms,
                config_meta=config["_meta"])
            research_rows.append(rec)
            research_keys[rec["value_keys_hash"]] = (vk, ck)
            support_symbols_written.add(symbol)

    meta = config["_meta"]
    envelope = {
        "generated_at": _iso(now_ms),
        "last_closed_minute": _iso(last_minute),
        "cycle_seconds": int(config["cycle_seconds"]),
        "config_version": meta["version"],
        "config_hash": meta["config_hash"],
        "score_version": v1_spec["version"] if engine == "v1" else config["score"]["version"],
        "score_status": v1_spec["status"] if engine == "v1" else config["score"]["status"],
        "active_engine": engine,
        "engines": {"v0": {"version": config["score"]["version"], "status": config["score"]["status"]},
                    "v1": {"version": v1_spec["version"], "status": v1_spec["status"],
                           "regime": _json_safe(v1_out.get("regime")),
                           "members": v1.members(v1_out.get("results") or {})}},
        "capital_flow": _json_safe(capital),
        "pool_id": str(pool_id),
        "total_assets": len(rows),
        "failed_assets": len(failures),
        "cycle_duration_ms": None,
        "rows": rows,
        "universe_filter": {**config["universe_filter"], "candidate_assets": len(candidates),
                            "eligible_assets": len(symbols),
                            "excluded_assets": len(candidates) - len(symbols),
                            "label_drain_assets": len(drain_symbols),
                            "market_cap_source": "market_metadata",
                            "market_cap_freshness": "not_certified"},
    }

    sync_report = await _sync_realtime_pools(user_id, rows, config, now_ms, state,
                                             engine=engine, v1_results=v1_out.get("results") or {})
    envelope["cycle_duration_ms"] = int((time.monotonic() - started) * 1000)
    envelope["realtime_sync"] = sync_report

    sample = cycle_index % max(1, int(config["snapshots"]["every_n_cycles"])) == 0

    async def _write(db):
        if new_alert_rows:
            await db.execute(text("""
                INSERT INTO pump_monitor_alerts (symbol, alert_type, triggered_at, inputs, config_version, config_hash)
                VALUES (:symbol, :alert_type, :triggered_at, CAST(:inputs AS jsonb), :v, :h)
            """), [{**a, "v": meta["version"], "h": meta["config_hash"]} for a in new_alert_rows])
        if sample:
            await db.execute(text("""
                INSERT INTO pump_monitor_snapshots (cycle_at, pool_id, symbol, config_version, config_hash, score, row)
                VALUES (:cycle_at, CAST(:pool AS uuid), :symbol, :v, :h, :score, CAST(:row AS jsonb))
            """), [{"cycle_at": datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc), "pool": str(pool_id),
                    "symbol": r["symbol"], "v": meta["version"], "h": meta["config_hash"],
                    "score": r["pump_monitor_score"], "row": json.dumps(_compact(r))} for r in rows])
    await run_db_task(_write, celery=True)

    # Capital-flow history in its own transaction: a missing table (migration not
    # yet applied) or any write failure never breaks the cycle.
    if capital and cap_minutes and capital.get("state") not in (None, "desligado"):
        try:
            last_cap = cap_minutes[-1]

            async def _write_capital(db):
                await db.execute(_UPSERT_CAPITAL, capital_row(user_id, last_cap, capital, int(meta["version"])))
            await run_db_task(_write_capital, celery=True)
        except Exception as exc:
            logger.warning("[PUMP-CAPITAL] history write failed user=%s reason=%s", user_id, type(exc).__name__)

    if research_rows:
        members = set()
        for pool_state in (state.get("membership") or {}).values():
            members |= set((pool_state or {}).get("members") or {})
        for rec in research_rows:
            rec["pool_member"] = rec["symbol"] in members if rec["symbol"] in symbols else False
        if await research.write_safely(run_db_task, research_rows, research_keys):
            if research_due:
                state["research_last_minute"] = last_minute
            if support_due and set(drain_symbols) <= support_symbols_written:
                state["research_support_last_minute"] = last_minute

    if redis is not None:
        try:
            ttl = int(config["cycle_seconds"]) * 10
            await redis.set(_LATEST_KEY.format(user_id=user_id), json.dumps(envelope), ex=ttl)
            state.update(breakout=breakout_state, alerts=alert_state, cycle_index=cycle_index,
                         score_v1=v1_state)
            await redis.set(state_key, json.dumps(state), ex=86_400)
        except Exception as exc:
            logger.warning("[PUMP-MONITOR] redis write failed: %s", exc)

    logger.info("[PUMP-MONITOR] user=%s assets=%d failed=%d alerts=%d duration_ms=%d sampled=%s",
                user_id, len(rows), len(failures), len(new_alert_rows),
                envelope["cycle_duration_ms"], sample)
    logger.info("[PUMP-UNIVERSE] candidates=%d eligible=%d excluded=%d label_drain=%d cap_min_usd=%s cap_freshness=not_certified",
                len(candidates), len(symbols), len(candidates) - len(symbols), len(drain_symbols),
                config["universe_filter"]["min_market_cap_usd"])
    # Pump-only additive capture AFTER the legacy cycle, sync and persistence.
    # A failure here cannot change any legacy score, membership or Shadow state.
    from . import pump_opportunity_service as opportunities
    # Independent transactions: a capture timeout must not starve already-due
    # labels. Each operation keeps its own existing deadline and resource caps.
    for stage,operation in (
        ("capture",lambda:opportunities.ingest(user_id, rows, collected, opportunity_source_meta, config)),
        ("labels",lambda:opportunities.label_batch(user_id)),
    ):
        started=time.monotonic()
        try:
            await operation()
        except Exception as exc:
            logger.warning("[PUMP-OPPORTUNITY] isolated stage failure user=%s stage=%s reason=%s duration_ms=%d",
                user_id, stage, type(exc).__name__, int((time.monotonic()-started)*1000))
    return {"symbols": len(rows), "failed": len(failures), "alerts": len(new_alert_rows),
            "duration_ms": envelope["cycle_duration_ms"]}


V1_CELL_KEYS = ("progress_atr", "rs_atr", "extension_atr", "efficiency_short", "efficiency_long", "consistency",
                "higher_lows", "concentration", "wick", "rvol_5m", "volume_spike_max", "compression_ratio",
                "progress_1m_atr")


def pump_ml_inference_step(params: Dict[str, Any]) -> int:
    return _TF_SECONDS[params["timeframe"]]


def context_cells(v1_out: Dict[str, Any], v1_spec: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """ML-independent v1 context per symbol, as indicator cells.

    Single source for both the stored observations (via ``apply_engines``) and
    the ML pre-pass, so training and inference read identical values. Uses the
    PRICE regime and raw capital tide, never the ML-capped regime or score.
    """
    results = v1_out.get("results") or {}
    reg = v1_out.get("regime") or {}
    cap = reg.get("capital") or {}
    universe = {"ctx_breadth": reg.get("breadth"), "ctx_ref_progress_atr": reg.get("reference_progress_atr"),
                "ctx_ref_ret_pct": reg.get("reference_ret_pct"), "ctx_capital_ratio": cap.get("ratio"),
                "ctx_capital_z": cap.get("z")}
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for symbol, res in results.items():
        safe = _json_safe(res)
        cells: Dict[str, Dict[str, Any]] = {}
        for key in V1_CELL_KEYS:
            value = (safe.get("values") or {}).get(key)
            cells[f"v1_{key}"] = {"value": value, "reason": None if value is not None else safe.get("structure_reason"),
                                  "source": f"{v1_spec['version']}:{v1_spec['structure']['timeframe']}_closed"}
        deriv = safe.get("derivatives") or {}
        for key in ("perp_flow_norm", "oi_change_pct", "funding_rate", "short_liq_oi_bps"):
            cells[f"perp_{key}"] = {"value": deriv.get(key), "reason": None if deriv.get(key) is not None
                                    else (deriv.get("reason") or "no_data"), "source": "gate_futures_contract_stats"}
        for key, value in universe.items():
            value = _json_safe(value)
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
            cells[key] = {"value": value if ok else None, "reason": None if ok else "regime_unavailable",
                          "source": f"{v1_spec['version']}:universe_regime"}
        out[symbol] = cells
    return out


def apply_engines(rows: List[Dict[str, Any]], v1_out: Dict[str, Any], engine: str, v1_spec: Dict[str, Any]) -> None:
    """Attach v1 to each row and set the displayed Pump Score cell.

    v0 top-level fields (``pump_monitor_score``, ``score_components``, exhaustion)
    are never touched: the research dataset and the continuity contract keep
    reading v0. Only ``indicators.pump_monitor_score`` (the displayed cell) and
    ``only_rising`` follow the active engine.
    """
    results = v1_out.get("results") or {}
    context = context_cells(v1_out, v1_spec)
    for row in rows:
        cells = row["indicators"]
        v0_cell = dict(cells.get("pump_monitor_score") or {})
        cells["pump_score_v0"] = {**v0_cell, "source": "pump_monitor_score_v0"}
        res = results.get(row["symbol"])
        if res is None:
            cells["pump_score_v1"] = {"value": None, "reason": "v1_unavailable", "source": v1_spec["version"],
                                      "status": "NO_DATA"}
            row["score_v1"] = None
        else:
            safe = _json_safe(res)
            row["score_v1"] = safe
            cells["pump_score_v1"] = {"value": safe["score"], "reason": safe["reason"], "source": v1_spec["version"],
                                      "status": "VALID" if safe["score"] is not None else "NO_DATA",
                                      "state": safe["state"], "condition": safe["condition"]}
            cells.update(context.get(row["symbol"]) or {})
            row["direction"] = safe.get("direction")
            p_up = safe.get("ml_up_probability")
            cells["ml_up_probability"] = {"value": p_up, "reason": None if p_up is not None else "ml_inactive_or_abstained",
                                          "source": "pump_ml_directional"}
        if engine == "v1":
            shown = cells["pump_score_v1"]
            cells["pump_monitor_score"] = {**shown, "color_state": v0_cell.get("color_state")}
            row["only_rising"] = bool(res) and (res["state"] in v1.LISTED_STATES or res["condition"] == "subindo")


def _compact(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": row["symbol"], "score": row["pump_monitor_score"],
        "score_confidence": row["score_confidence"], "only_rising": row["only_rising"],
        "values": {k: c.get("value") for k, c in row["indicators"].items()},
        "reasons": {k: c.get("reason") for k, c in row["indicators"].items() if c.get("reason")},
        "components": row["score_components"], "alerts": [a["type"] for a in row["alerts_active"]],
        "score_pre_veto": row.get("score_pre_veto"), "exhaustion_flag": row.get("exhaustion_flag"),
        "exhaustion_triggers": row.get("exhaustion_triggers"),
        "exhaustion_missing": row.get("exhaustion_missing"),
    }


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (UUID,)):
        return str(value)
    if hasattr(value, "__float__") and not isinstance(value, (bool, int, float)):
        return float(value)
    return value


# ── REALTIME sync (extends the Market Catalyst radar sync) ───────────────────

async def _sync_realtime_pools(user_id, rows, config, now_ms, state, *, engine: str = "v0",
                               v1_results: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    from ..database import run_db_task
    from .radar_feed_audit import record_receipt, complete_receipt
    from .radar_pool_sync import reconcile_radar_pool

    async def _pools(db):
        return (await db.execute(text("""
            SELECT id, name, overrides FROM pools
             WHERE user_id = :u AND is_active AND market_type = 'spot'
               AND (overrides->>'pump_monitor_sync_enabled')::boolean IS TRUE
        """), {"u": str(user_id)})).mappings().all()
    pools = await run_db_task(_pools, celery=True)
    membership = state.setdefault("membership", {})
    report = []
    scored = [r for r in rows if r.get("pump_monitor_score") is not None]
    for pool in pools:
        pool_id, overrides = str(pool["id"]), dict(pool["overrides"] or {})
        params = {k: overrides.get(f"pump_monitor_{k}", v) for k, v in config["sync_defaults"].items()}
        entry = {"pool_id": pool_id, "pool_name": pool["name"], "engine": engine}
        if overrides.get("observation_only") is not True:
            entry["skipped"] = "pool_not_observation_only"
            report.append(entry)
            continue
        previous = membership.get(pool_id) or {"members": {}}
        if engine == "v1":
            # v1 owns its hysteresis (state machine): members = ativo | enfraquecendo.
            results = v1_results or {}
            evaluated = [s for s, r in results.items() if r.get("structure_reason") is None]
            feed_ok = bool(rows) and len(evaluated) / len(rows) >= float(params["min_scored_fraction"])
            listed = [s for s in v1.members(results)]
            ranked = sorted(listed, key=lambda s: -(results[s].get("score") or 0.0))
            if feed_ok:
                kept = previous.get("members") or {}
                membership[pool_id] = {"members": {s: kept.get(s) or {"entered_at_ms": int(now_ms), "out_count": 0}
                                                   for s in listed}}
        else:
            # Membership is decided by the Pump Score alone (no "rising" pre-filter).
            ranked = [r["symbol"] for r in sorted(scored, key=lambda r: -r["pump_monitor_score"])]
            feed_ok = bool(rows) and len(scored) / len(rows) >= float(params["min_scored_fraction"])
            if feed_ok:
                membership[pool_id] = eng.advance_membership(
                    previous, {r["symbol"]: float(r["pump_monitor_score"]) for r in scored},
                    now_ms=now_ms, min_score=float(params["min_score"]),
                    exit_consecutive_cycles=int(params["exit_consecutive_cycles"]),
                    min_hold_seconds=int(params["min_hold_seconds"]))
        selected = set(membership.get(pool_id, {}).get("members") or {}) if feed_ok else None
        health = state.setdefault("sync_health", {})
        was_unavailable = health.get(pool_id) == "unavailable"
        # Receipts record transitions only (membership change, outage start/end).
        changed = (not was_unavailable) if selected is None else (
            was_unavailable or selected != set(previous.get("members") or {}))
        health[pool_id] = "ok" if feed_ok else "unavailable"
        reason = None if feed_ok else "insufficient_scored_assets"
        received_at = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
        rank_of = {s: i + 1 for i, s in enumerate(ranked)}

        async def _sync(db, pool_id=pool_id, selected=selected, reason=reason, changed=changed):
            receipt_id = None
            if changed:
                assets = None if selected is None else [
                    {"pair": s, "updated_at": received_at.isoformat(), "rank": rank_of.get(s)}
                    for s in sorted(selected, key=lambda s: rank_of.get(s, 10**6))]
                receipt_id = await record_receipt(db, pool_id=UUID(pool_id), assets=assets,
                                                  received_at=received_at, selected_pairs=selected or set(),
                                                  reason=reason, source_type="pump_monitor")
            stats = await reconcile_radar_pool(
                db, pool_id=UUID(pool_id), user_id=user_id, radar_pairs=selected, reason=reason,
                origin="pump_monitor", enabled_key="pump_monitor_sync_enabled",
                health_key="pump_monitor_feed_health", hold_open_positions=False)
            if receipt_id is not None and selected is not None:
                await complete_receipt(db, receipt_id, reconciled=True, skipped=stats.get("skipped", False))
            return stats
        try:
            entry["result"] = await run_db_task(_sync, celery=True)
            entry["members"] = sorted(selected) if selected is not None else None
        except Exception:
            logger.exception("[PUMP-MONITOR] REALTIME sync failed pool=%s", pool_id)
            entry["error"] = "sync_failed"
        report.append(entry)
    return report


# ── Retention ────────────────────────────────────────────────────────────────

async def purge(db) -> Dict[str, int]:
    configs = await _enabled_configs(db)
    days = max([int(c["flow"]["retention_days"]) for _, c in configs] or [int(eng.DEFAULT_CONFIG["flow"]["retention_days"])])
    hours = max([int(c["snapshots"]["retention_hours"]) for _, c in configs] or [int(eng.DEFAULT_CONFIG["snapshots"]["retention_hours"])])
    now = datetime.now(timezone.utc)
    b = await db.execute(text("DELETE FROM flow_buckets_1m WHERE bucket_start < :t"), {"t": now - timedelta(days=days)})
    s = await db.execute(text("DELETE FROM pump_monitor_snapshots WHERE cycle_at < :t"), {"t": now - timedelta(hours=hours)})
    cap_days = max([int(((c.get("score_v1") or {}).get("capital_flow") or {}).get("history_retention_days") or 0)
                    for _, c in configs]
                   or [int(v1.DEFAULT_V1["capital_flow"]["history_retention_days"])])
    try:
        async with db.begin_nested():
            cf = await db.execute(text("DELETE FROM pump_capital_flow_1m WHERE minute < :t"),
                                  {"t": now - timedelta(days=max(1, cap_days))})
        capital_purged = cf.rowcount
    except Exception as exc:
        logger.warning("[PUMP-CAPITAL] retention skipped reason=%s", type(exc).__name__)
        capital_purged = None
    live_days = max([int((((c.get("score_v1") or {}).get("ml") or {}).get("live_log") or {}).get("retention_days") or 0)
                     for _, c in configs]
                    or [int(v1.DEFAULT_V1["ml"]["live_log"]["retention_days"])])
    try:
        async with db.begin_nested():
            lp = await db.execute(text("DELETE FROM pump_ml_live_predictions WHERE decision_at < :t"),
                                  {"t": now - timedelta(days=max(1, live_days))})
        live_purged = lp.rowcount
    except Exception as exc:
        logger.warning("[PUMP-ML] live log retention skipped reason=%s", type(exc).__name__)
        live_purged = None
    hist_days = max([int((c.get("flow_history") or {}).get("retention_days") or 0) for _, c in configs]
                    or [int(eng.DEFAULT_CONFIG["flow_history"]["retention_days"])])
    history_purged: Optional[Dict[str, int]] = None
    try:
        from .pump_flow_history import retention_cutoff
        cutoff = retention_cutoff(now, hist_days)
        async with db.begin_nested():
            fl = await db.execute(text("DELETE FROM pump_flow_5m WHERE bucket_start < :t"), {"t": cutoff})
            pp = await db.execute(text("DELETE FROM pump_perp_stats_5m WHERE stat_time < :t"), {"t": cutoff})
            bk = await db.execute(text("DELETE FROM pump_book_5m WHERE bucket_start < :t"), {"t": cutoff})
            fa = await db.execute(text("DELETE FROM pump_flow_asof WHERE decision_at < :t"), {"t": cutoff})
        history_purged = {"flow_5m": fl.rowcount, "perp_stats_5m": pp.rowcount, "book_5m": bk.rowcount,
                          "flow_asof": fa.rowcount}
    except Exception as exc:
        logger.warning("[PUMP-FLOW-HISTORY] retention skipped reason=%s", type(exc).__name__)
    return {"buckets": b.rowcount, "snapshots": s.rowcount, "capital_flow": capital_purged,
            "flow_history": history_purged,
            "ml_live_predictions": live_purged}


# ── API reads ────────────────────────────────────────────────────────────────

async def latest_envelope(db, user_id) -> Optional[Dict[str, Any]]:
    """Latest full cycle from Redis; the sampled table is only a fallback."""
    envelope = await _read_json(await _redis(), _LATEST_KEY.format(user_id=user_id))
    if envelope:
        envelope["served_from"] = "redis_latest_cycle"
        return envelope
    row = (await db.execute(text("""
        SELECT s.cycle_at FROM pump_monitor_snapshots s
         JOIN pools p ON p.id = s.pool_id WHERE p.user_id = :u
         ORDER BY s.cycle_at DESC LIMIT 1
    """), {"u": str(user_id)})).scalar()
    if row is None:
        return None
    snaps = (await db.execute(text("""
        SELECT s.row, s.config_version, s.config_hash, s.pool_id FROM pump_monitor_snapshots s
         JOIN pools p ON p.id = s.pool_id WHERE p.user_id = :u AND s.cycle_at = :t
    """), {"u": str(user_id), "t": row})).mappings().all()
    return {"generated_at": row.isoformat(), "served_from": "sampled_snapshot_table",
            "compact_rows": [dict(s["row"]) for s in snaps],
            "config_version": snaps[0]["config_version"] if snaps else None,
            "config_hash": snaps[0]["config_hash"] if snaps else None,
            "pool_id": str(snaps[0]["pool_id"]) if snaps else None, "rows": []}


def select_rows(envelope: Dict[str, Any], *, limit: int, sort: str, order: str,
                only_rising: bool, columns: Optional[List[str]], config: Dict[str, Any]) -> Dict[str, Any]:
    rows = list(envelope.get("rows") or [])
    if only_rising:
        rows = [r for r in rows if r.get("only_rising")]

    def _key(r):
        if sort == "pump_monitor_score":
            # the displayed cell carries the active engine's score (v0 or v1)
            cell = (r.get("indicators") or {}).get("pump_monitor_score") or {}
            v = cell.get("value") if "value" in cell else r.get("pump_monitor_score")
        elif sort == "symbol":
            return (0, r.get("symbol") or "")
        else:
            v = ((r.get("indicators") or {}).get(sort) or {}).get("value")
        return (1, 0.0) if not isinstance(v, (int, float)) or isinstance(v, bool) else (0, float(v))

    if sort == "symbol":
        rows.sort(key=_key, reverse=(order == "desc"))
    else:
        present = [r for r in rows if _key(r)[0] == 0]
        missing = [r for r in rows if _key(r)[0] == 1]
        present.sort(key=lambda r: _key(r)[1], reverse=(order == "desc"))
        rows = present + missing  # nulls always last, never ranked as zero
    if limit:
        rows = rows[:limit]
    if envelope.get("active_engine") == "v1":
        objective = (((config or {}).get("score_v1") or {}).get("ml") or {}).get("objective", "relative")
        rows = [{**r, "score_version": envelope.get("score_version"), "score_confidence": None,
                 "score_components": v1.display_components(r["score_v1"], objective)} if r.get("score_v1") else r
                for r in rows]
    if columns:
        wanted = _expand_columns(columns, config)
        rows = [{**r, "indicators": {k: v for k, v in (r.get("indicators") or {}).items() if k in wanted}}
                for r in rows]
    age = None
    try:
        age = round((datetime.now(timezone.utc) - datetime.fromisoformat(envelope["generated_at"])).total_seconds(), 1)
    except (KeyError, TypeError, ValueError):
        pass
    return {**{k: v for k, v in envelope.items() if k != "rows"}, "data_age_seconds": age, "rows": rows}


def _expand_columns(columns: List[str], config: Dict[str, Any]) -> set:
    groups: Dict[str, set] = {}
    for name, spec in (config.get("indicators") or {}).items():
        groups.setdefault(spec.get("group") or "other", set()).add(name)
    wanted = set()
    for c in columns:
        wanted |= groups.get(c, {c})
    return wanted
