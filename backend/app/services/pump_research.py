"""Pump Monitor research dataset — per-minute rows and offline labels.

Separate from the live score on purpose: the cycle only *records* what it
already computed (one narrow row per asset per minute, written after the
score and the pool sync, failures swallowed); labels are produced by an
hourly job on the research worker, only for rows older than
``t + max horizon + settle``, so nothing written in the live path can see the
future. Every parameter comes from ``pump_monitor.research`` (versioned).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text

from . import flow_metrics as fm

logger = logging.getLogger(__name__)

MINUTE_MS = 60_000


def _canonical_hash(value: Any) -> str:
    from .profile_runtime_config import canonical_hash
    return canonical_hash(value)


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


# ── Live path: build one row (pure) ──────────────────────────────────────────

def value_keys_hash(value_keys: Iterable[str], contribution_keys: Iterable[str]) -> str:
    return _canonical_hash({"values": list(value_keys), "contributions": list(contribution_keys)})


def research_row(row: Dict[str, Any], *, minute_ms: int, bucket: Optional[Dict[str, Any]],
                 book: Optional[Dict[str, Any]], cycle_at_ms: int,
                 config_meta: Dict[str, Any]) -> Tuple[Dict[str, Any], Tuple[str, ...], Tuple[str, ...]]:
    """One narrow dataset row from an already-built monitor row. No new computation."""
    cells = row["indicators"]
    value_keys = tuple(sorted(cells))
    contribution_keys = tuple(sorted(row.get("score_components") or {}))
    vals, categorical, reasons = [], {}, {}
    for key in value_keys:
        value = cells[key].get("value")
        num = _num(value)
        vals.append(num)
        if value is not None and num is None:
            categorical[key] = value
        if value is None and cells[key].get("reason"):
            reasons[key] = cells[key]["reason"]
    comps = row.get("score_components") or {}
    contributions = [_num((comps[k] or {}).get("contribution")) for k in contribution_keys]

    bids, asks = fm._levels((book or {}).get("bids")), fm._levels((book or {}).get("asks"))
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    bucket = bucket or {}
    partial = bucket.get("partial")
    close = bucket.get("close_price") if not partial else None
    snapshot_price = _num((cells.get("price") or {}).get("value"))
    record = {
        "symbol": row["symbol"],
        "ts": datetime.fromtimestamp(minute_ms / 1000.0, tz=timezone.utc),
        "cycle_at": datetime.fromtimestamp(cycle_at_ms / 1000.0, tz=timezone.utc),
        "config_version": int(config_meta.get("version") or 0),
        "config_hash": config_meta.get("config_hash") or "",
        "value_keys_hash": value_keys_hash(value_keys, contribution_keys),
        "price": close if close is not None else snapshot_price,
        "mid": fm.book_mid((book or {}).get("bids"), (book or {}).get("asks")) if book else None,
        "best_bid": best_bid, "best_ask": best_ask,
        "bucket_open": bucket.get("open_price"), "bucket_high": bucket.get("high_price"),
        "bucket_low": bucket.get("low_price"), "bucket_close": bucket.get("close_price"),
        "bucket_partial": partial,
        "spread_pct": _num((cells.get("spread_pct") or {}).get("value")),
        "slippage_buy_pct": _num((cells.get("estimated_slippage_buy_pct") or {}).get("value")),
        "slippage_sell_pct": _num((cells.get("estimated_slippage_sell_pct") or {}).get("value")),
        "insufficient_depth_buy": (cells.get("estimated_slippage_buy_pct") or {}).get("reason") == "insufficient_depth",
        "insufficient_depth_sell": (cells.get("estimated_slippage_sell_pct") or {}).get("reason") == "insufficient_depth",
        "score": _num(row.get("pump_monitor_score")),
        "score_pre_veto": _num(row.get("score_pre_veto")),
        "score_confidence": _num(row.get("score_confidence")),
        "exhaustion_flag": row.get("exhaustion_flag"),
        "exhaustion_triggers": list(row.get("exhaustion_triggers") or []),
        "alerts": [a["type"] for a in row.get("alerts_active") or []],
        "pool_member": False,
        "data_age_seconds": _num(row.get("data_age_seconds")),
        "vals": vals,
        "contributions": contributions,
        "categorical": categorical or None,
        "null_reasons": reasons or None,
    }
    return record, value_keys, contribution_keys


def is_research_minute(minute_ms: int, last_written_ms: Optional[int], spec: Dict[str, Any]) -> bool:
    if not spec.get("enabled") or last_written_ms == minute_ms:
        return False
    return (minute_ms // MINUTE_MS) % max(1, int(spec["every_n_minutes"])) == 0


_INSERT_ROW = text("""
    INSERT INTO pump_research_minute (symbol, ts, cycle_at, config_version, config_hash, value_keys_hash,
        price, mid, best_bid, best_ask, bucket_open, bucket_high, bucket_low, bucket_close, bucket_partial,
        spread_pct, slippage_buy_pct, slippage_sell_pct, insufficient_depth_buy, insufficient_depth_sell,
        score, score_pre_veto, score_confidence, exhaustion_flag, exhaustion_triggers, alerts, pool_member,
        data_age_seconds, vals, contributions, categorical, null_reasons)
    VALUES (:symbol, :ts, :cycle_at, :config_version, :config_hash, :value_keys_hash,
        :price, :mid, :best_bid, :best_ask, :bucket_open, :bucket_high, :bucket_low, :bucket_close, :bucket_partial,
        :spread_pct, :slippage_buy_pct, :slippage_sell_pct, :insufficient_depth_buy, :insufficient_depth_sell,
        :score, :score_pre_veto, :score_confidence, :exhaustion_flag, :exhaustion_triggers, :alerts, :pool_member,
        :data_age_seconds, :vals, :contributions, CAST(:categorical AS jsonb), CAST(:null_reasons AS jsonb))
    ON CONFLICT (symbol, ts) DO NOTHING
""")


async def write_minute_rows(db, records: List[Dict[str, Any]], keys: Dict[str, Tuple[tuple, tuple]]) -> int:
    for h, (value_keys, contribution_keys) in keys.items():
        await db.execute(text("""
            INSERT INTO pump_research_value_keys (value_keys_hash, value_keys, contribution_keys)
            VALUES (:h, :v, :c) ON CONFLICT (value_keys_hash) DO NOTHING
        """), {"h": h, "v": list(value_keys), "c": list(contribution_keys)})
    params = [{**r, "categorical": json.dumps(r["categorical"]) if r["categorical"] is not None else None,
               "null_reasons": json.dumps(r["null_reasons"]) if r["null_reasons"] is not None else None}
              for r in records]
    if params:
        await db.execute(_INSERT_ROW, params)
    return len(params)


async def write_safely(run_db_task, records, keys) -> bool:
    """Never lets a dataset failure reach the live cycle: log and report."""
    try:
        await run_db_task(lambda db: write_minute_rows(db, records, keys), celery=True)
        return True
    except Exception as exc:
        logger.warning("[PUMP-RESEARCH] minute write failed rows=%d: %s", len(records), type(exc).__name__)
        return False


def price_support_row(symbol, *, minute_ms, bucket, cycle_at_ms, config_meta):
    """Endpoint evidence only: never a new observation, score or label target."""
    record, vk, ck = research_row({"symbol": symbol, "indicators": {}},
                                 minute_ms=minute_ms, bucket=bucket, book=None,
                                 cycle_at_ms=cycle_at_ms, config_meta=config_meta)
    record["categorical"] = {"_research_role": "label_drain"}
    return record, vk, ck


# ── Offline labels (pure) ────────────────────────────────────────────────────

def max_horizon_minutes(labels: Dict[str, Any]) -> int:
    return max([int(h) for h in labels["horizons_minutes"]] +
               [int(b["h_minutes"]) for b in labels["barriers"].values()])


def label_cutoff(now: datetime, labels: Dict[str, Any]) -> datetime:
    """Latest ``ts`` that may be labelled at ``now`` (no future leakage)."""
    return now - timedelta(minutes=max_horizon_minutes(labels), seconds=int(labels["settle_seconds"]))


def _bar(series: Dict[int, Dict[str, Any]], minute_ms: int) -> Optional[Dict[str, float]]:
    b = series.get(minute_ms)
    if not b or b.get("bucket_partial"):
        return None
    vals = {k: _num(b.get(f"bucket_{k}")) for k in ("high", "low", "close")}
    return None if None in vals.values() else vals


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0


def compute_labels(series: Dict[int, Dict[str, Any]], *, t_ms: int, cost_pct: Optional[float],
                   labels: Dict[str, Any]) -> Dict[str, Any]:
    """Returns, path (MFE/MAE/t_peak) and triple barriers for the row at ``t_ms``.

    ``series`` maps minute start (ms) → recorded row (bucket OHLC). Missing or
    partial minutes are gaps, never interpolated. A horizon with more than
    ``max_gap_pct`` missing minutes is null (``price_gap``). Barriers are net
    of ``cost_pct``; a minute that touches TP and SL counts as SL.
    """
    entry = _bar(series, t_ms)
    out = {"price_t": entry["close"] if entry else None, "returns": {}, "path": {}, "barriers": {}, "reason": None}
    if entry is None:
        out["reason"] = "no_entry_price"
        return out
    p0 = entry["close"]
    max_gap = float(labels["max_gap_pct"])

    def window(h):
        bars = [(k, _bar(series, t_ms + k * MINUTE_MS)) for k in range(1, h + 1)]
        missing = sum(1 for _, b in bars if b is None)
        return bars, 100.0 * missing / h > max_gap

    for h in sorted({int(x) for x in labels["horizons_minutes"]}):
        bars, gap = window(h)
        avail = [b for _, b in bars if b]
        if gap or not avail:
            out["returns"][str(h)] = {"gross": None, "net": None, "reason": "price_gap"}
            out["path"][str(h)] = {"mfe": None, "mae": None, "reason": "price_gap"}
            continue
        out["path"][str(h)] = {"mfe": round(_pct(max(b["high"] for b in avail), p0), 6),
                               "mae": round(_pct(min(b["low"] for b in avail), p0), 6), "reason": None}
        end = bars[-1][1]
        if end is None:  # the return needs the exact close at t + h; never interpolated
            out["returns"][str(h)] = {"gross": None, "net": None, "reason": "price_gap_endpoint"}
            continue
        gross = _pct(end["close"], p0)
        out["returns"][str(h)] = {"gross": round(gross, 6),
                                  "net": None if cost_pct is None else round(gross - cost_pct, 6),
                                  "reason": None if cost_pct is not None else "cost_unavailable"}
    h_peak = max(int(x) for x in labels["horizons_minutes"])
    bars, gap = window(h_peak)
    avail = [(k, b) for k, b in bars if b]
    out["path"]["t_peak_minutes"] = None if gap or not avail else max(avail, key=lambda kb: (kb[1]["high"], -kb[0]))[0]

    for name, b in labels["barriers"].items():
        h = int(b["h_minutes"])
        bars, gap = window(h)
        if gap:
            out["barriers"][name] = {"y": None, "t_touch": None, "r_at_exit": None, "reason": "price_gap"}
            continue
        if cost_pct is None:
            out["barriers"][name] = {"y": None, "t_touch": None, "r_at_exit": None, "reason": "cost_unavailable"}
            continue
        tp, sl = float(b["tp_pct"]), float(b["sl_pct"])
        result = None
        for k, bar in bars:
            if bar is None:
                continue
            hit_sl = _pct(bar["low"], p0) - cost_pct <= -sl
            hit_tp = _pct(bar["high"], p0) - cost_pct >= tp
            if hit_sl:  # also covers TP and SL in the same minute
                result = {"y": 0, "t_touch": k, "r_at_exit": -sl, "reason": None}
                break
            if hit_tp:
                result = {"y": 1, "t_touch": k, "r_at_exit": tp, "reason": None}
                break
        if result is None:
            end = bars[-1][1]
            result = {"y": -1, "t_touch": None,
                      "r_at_exit": None if end is None else round(_pct(end["close"], p0) - cost_pct, 6),
                      "reason": None if end is not None else "price_gap_endpoint"}
        out["barriers"][name] = result
    return out


def row_cost_pct(row: Dict[str, Any], fee_roundtrip_pct: Optional[float]) -> Optional[float]:
    buy, sell = _num(row.get("slippage_buy_pct")), _num(row.get("slippage_sell_pct"))
    if buy is None or sell is None or fee_roundtrip_pct is None:
        return None
    return buy + sell + float(fee_roundtrip_pct)


# ── Offline labels (DB) ──────────────────────────────────────────────────────

async def load_fee_roundtrip(db, source: Dict[str, Any]) -> Optional[float]:
    row = (await db.execute(text("""
        SELECT config_json->>:k AS fee FROM config_profiles
         WHERE config_type = :t AND is_active IS NOT FALSE AND config_json ? :k
         ORDER BY updated_at DESC NULLS LAST LIMIT 1
    """), {"k": source["key"], "t": source["config_type"]})).first()
    return _num(float(row.fee)) if row and row.fee is not None else None


async def label_pending(db, research: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    labels = research["labels"]
    now = now or datetime.now(timezone.utc)
    cutoff = label_cutoff(now, labels)
    fee = await load_fee_roundtrip(db, labels["fee_roundtrip_source"])
    version = labels["version"]
    label_hash = _canonical_hash({"labels": labels, "fee_roundtrip_pct": fee})
    pending = (await db.execute(text("""
        SELECT m.symbol, m.ts, m.slippage_buy_pct, m.slippage_sell_pct FROM pump_research_minute m
         WHERE m.ts <= :cutoff
           AND COALESCE(m.categorical->>'_research_role', '') <> 'label_drain'
           AND NOT EXISTS (
               SELECT 1 FROM pump_research_labels l
                WHERE l.symbol = m.symbol AND l.ts = m.ts AND l.label_set_version = :v)
         ORDER BY m.ts LIMIT :n
    """), {"cutoff": cutoff, "v": version, "n": int(labels["batch_rows"])})).mappings().all()
    if not pending:
        return {"labelled": 0, "cutoff": cutoff.isoformat(), "fee_roundtrip_pct": fee}
    h_max = max_horizon_minutes(labels)
    lo, hi = min(r["ts"] for r in pending), max(r["ts"] for r in pending) + timedelta(minutes=h_max)
    symbols = sorted({r["symbol"] for r in pending})
    bars = (await db.execute(text("""
        SELECT symbol, ts, bucket_high, bucket_low, bucket_close, bucket_partial FROM pump_research_minute
         WHERE symbol = ANY(CAST(:s AS text[])) AND ts >= :lo AND ts <= :hi
    """), {"s": symbols, "lo": lo, "hi": hi})).mappings().all()
    series: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for b in bars:
        series.setdefault(b["symbol"], {})[int(b["ts"].timestamp() * 1000)] = dict(b)
    out = []
    for r in pending:
        t_ms = int(r["ts"].timestamp() * 1000)
        cost = row_cost_pct(r, fee)
        lab = compute_labels(series.get(r["symbol"], {}), t_ms=t_ms, cost_pct=cost, labels=labels)
        out.append({"symbol": r["symbol"], "ts": r["ts"], "v": version, "labeled_at": now, "h": label_hash,
                    "price_t": lab["price_t"], "cost": cost, "fee": fee,
                    "returns": json.dumps(lab["returns"]), "path": json.dumps(lab["path"]),
                    "barriers": json.dumps(lab["barriers"]), "reason": lab["reason"]})
    await db.execute(text("""
        INSERT INTO pump_research_labels (symbol, ts, label_set_version, labeled_at, label_config_hash, price_t,
            cost_pct, fee_roundtrip_pct, returns, path, barriers, reason)
        VALUES (:symbol, :ts, :v, :labeled_at, :h, :price_t, :cost, :fee, CAST(:returns AS jsonb),
            CAST(:path AS jsonb), CAST(:barriers AS jsonb), :reason)
        ON CONFLICT (symbol, ts, label_set_version) DO NOTHING
    """), out)
    return {"labelled": len(out), "cutoff": cutoff.isoformat(), "fee_roundtrip_pct": fee}


# ── Partitions and retention ─────────────────────────────────────────────────

_TABLES = ("pump_research_minute", "pump_research_labels")


async def maintain(db, research: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    created, dropped, skipped = [], [], []
    for table in _TABLES:
        for offset in range(0, int(research["partition_days_ahead"]) + 1):
            d = today + timedelta(days=offset)
            name = f"{table}_{d:%Y%m%d}"
            start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            exists = (await db.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})).scalar()
            if exists:
                continue
            in_default = (await db.execute(text(
                f"SELECT EXISTS (SELECT 1 FROM {table}_default WHERE ts >= :a AND ts < :b)"),
                {"a": start, "b": start + timedelta(days=1)})).scalar()
            if in_default:
                skipped.append(name)
                logger.warning("[PUMP-RESEARCH] partition %s skipped: rows already in default", name)
                continue
            # DDL takes no bind parameters; bounds are generated here, never user input.
            end = start + timedelta(days=1)
            await db.execute(text(f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {table} "
                                  f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"))
            created.append(name)
        keep_from = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - timedelta(
            days=int(research["retention_days"]))
        parts = (await db.execute(text("""
            SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
             JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = :t
        """), {"t": table})).scalars().all()
        for name in parts:
            suffix = name.rsplit("_", 1)[-1]
            if not suffix.isdigit() or len(suffix) != 8:
                continue
            day = datetime.strptime(suffix, "%Y%m%d").replace(tzinfo=timezone.utc)
            if day + timedelta(days=1) <= keep_from:
                await db.execute(text(f"DROP TABLE IF EXISTS {name}"))
                dropped.append(name)
        await db.execute(text(f"DELETE FROM {table}_default WHERE ts < :k"), {"k": keep_from})
    return {"created": created, "dropped": dropped, "skipped": skipped}
