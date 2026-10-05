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


_TF_SQL_INTERVAL = {"1m": "1 minute", "5m": "5 minutes", "15m": "15 minutes", "1h": "1 hour"}


async def _load_candle_series(db, symbols: List[str], timeframe: str, count: int) -> Dict[str, List[Dict[str, Any]]]:
    """Last ``count`` CLOSED spot candles per symbol, ascending. One row per timestamp:
    Gate is preferred when another exchange wrote the same candle (fallback rows keep
    their ``exchange`` as provenance)."""
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (symbol, time) symbol, time, exchange, open, high, low, close, volume
          FROM ohlcv
         WHERE symbol = ANY(CAST(:s AS text[])) AND timeframe = :tf
           AND market_type = 'spot' AND is_closed IS TRUE
           AND time >= now() - (CAST(:interval AS interval) * :n)
         ORDER BY symbol, time, CASE WHEN exchange ILIKE 'gate%' THEN 0 ELSE 1 END
    """), {"s": symbols, "tf": timeframe, "interval": _TF_SQL_INTERVAL[timeframe], "n": int(count) + 2})).mappings().all()
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
                await _load_alpha(db, symbols),
                await _load_candle_series(db, sorted(set(symbols) | {v1_ref}),
                                          v1_spec["structure"]["timeframe"],
                                          int(v1_spec["structure"]["lookback_candles"])))
    buckets_by_symbol, merged, candles, alpha, series = await run_db_task(_load, celery=True)

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
    try:
        structures = {sym: v1.structure_metrics(series.get(sym) or [], v1_spec, now_ms)
                      for sym in set(symbols) | {v1_ref}}
        v1_out = v1.evaluate_universe(rows, structures, structures.get(v1_ref), v1_state,
                                      minute_ms=last_minute, spec=v1_spec)
    except Exception:
        logger.exception("[PUMP-SCORE-V1] evaluation failed user=%s", user_id)
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


def apply_engines(rows: List[Dict[str, Any]], v1_out: Dict[str, Any], engine: str, v1_spec: Dict[str, Any]) -> None:
    """Attach v1 to each row and set the displayed Pump Score cell.

    v0 top-level fields (``pump_monitor_score``, ``score_components``, exhaustion)
    are never touched: the research dataset and the continuity contract keep
    reading v0. Only ``indicators.pump_monitor_score`` (the displayed cell) and
    ``only_rising`` follow the active engine.
    """
    results = v1_out.get("results") or {}
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
            for key in V1_CELL_KEYS:
                value = (safe.get("values") or {}).get(key)
                cells[f"v1_{key}"] = {"value": value, "reason": None if value is not None else safe.get("structure_reason"),
                                      "source": f"{v1_spec['version']}:{v1_spec['structure']['timeframe']}_closed"}
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
    return {"buckets": b.rowcount, "snapshots": s.rowcount}


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
        rows = [{**r, "score_version": envelope.get("score_version"), "score_confidence": None,
                 "score_components": v1.display_components(r["score_v1"])} if r.get("score_v1") else r
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
