"""Read-only audit of PRICE_HISTORY_GAP in L3 managed-exit decisions.

A managed label is rejected with PRICE_HISTORY_GAP when two consecutive
persisted decisions (shadow_l3_exit_decisions) are not exactly one minute
apart. advance_shadow only evaluates the closed 1m candles that already exist
in ``ohlcv`` when it runs, and its cursor never revisits an earlier minute, so
a gap means: when minute t+k was evaluated, minute t was not in ``ohlcv``.

For every missing minute this script asks, from the database as it is now:
  MISSING_NOW     -- still no closed 1m candle in ohlcv for that minute;
  LATE_ARRIVAL    -- the candle exists, but was ingested after the decision
                     that skipped past it was processed;
  PRESENT_IN_TIME -- the candle was ingested before that decision was
                     processed (the skip is NOT explained by ingestion);
and, when the candle exists, whether it touched the stop/floor in force
before the gap or the TP (i.e. whether the recorded exit may be mis-timed).
It also reports the gaps by holding time, outcome, symbol and UTC hour, so
collector outages (many symbols, same minutes) are distinguishable from
per-symbol gaps. Nothing is written.

PowerShell (backend/, DATABASE_URL set):
  python -m scripts.audit_l3_decision_gaps --output l3_decision_gaps.json
"""
import argparse
import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

MINUTE = timedelta(minutes=1)
HOLDING_BUCKETS_H = (0.5, 1, 2, 4, 8, 16, 24.5)


def _bucket(hours):
    for edge in HOLDING_BUCKETS_H:
        if hours < edge:
            return f"<{edge}h"
    return f">={HOLDING_BUCKETS_H[-1]}h"


def _ts(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def gap_minutes(entry_at, decision_times):
    """Minutes the certifier expects but finds missing (same rule as certify)."""
    first = entry_at.replace(second=0, microsecond=0)
    if first != entry_at:
        first += MINUTE
    missing, previous = [], None
    for candle_at in decision_times:
        if candle_at < first:
            continue
        expected = previous + MINUTE if previous else first
        while expected < candle_at:
            missing.append((expected, candle_at))  # (missing minute, decision that skipped it)
            expected += MINUTE
        previous = candle_at
    return missing


async def _population(db, *, user_id, spec_hash, valid_from, cutoff, limit):
    from app.ml.l3_managed_exit import VERSION
    return (await db.execute(text(f"""
        SELECT st.id::text AS id, st.symbol, st.exchange, st.entry_timestamp, st.exit_timestamp, st.outcome,
               st.pnl_pct, st.holding_seconds, st.created_at, st.sl_price, st.tp_price,
               sx.state->'ml_label'->>'reason' AS persisted_reason,
               sx.state->'ml_label'->>'valid' AS persisted_valid
          FROM shadow_trades st
          LEFT JOIN shadow_l3_exit_states sx ON sx.shadow_id = st.id
         WHERE st.user_id = :uid AND st.source = 'L3' AND st.label_contract_version = :version
           AND st.config_snapshot->'l3_managed_ml'->'contract'->>'hash' = :hash
           AND st.entry_timestamp >= :valid_from AND st.outcome IS NOT NULL AND st.completed_at <= :cutoff
         ORDER BY st.created_at {('LIMIT ' + str(int(limit))) if limit else ''}
    """), {"uid": user_id, "version": VERSION, "hash": spec_hash, "valid_from": valid_from,
           "cutoff": cutoff})).mappings().all()


async def _decisions(db, ids):
    rows = await db.execute(text("""
        SELECT shadow_id::text AS shadow_id, candle_at,
               evidence->>'processed_at' AS processed_at,
               (state->>'floor_price')::double precision AS floor_price
          FROM shadow_l3_exit_decisions WHERE shadow_id = ANY(CAST(:ids AS uuid[]))
         ORDER BY shadow_id, candle_at"""), {"ids": ids})
    out = defaultdict(list)
    for r in rows.mappings():
        out[r["shadow_id"]].append(r)
    return out


async def _candles(db, exchange, keys, *, shadow_table):
    """keys: list of (symbol, minute). Returns {(symbol, minute): row}."""
    if not keys:
        return {}
    table = "ohlcv_shadow" if shadow_table else "ohlcv"
    rows = await db.execute(text(f"""
        SELECT q.symbol, q.t AS minute, o.high, o.low, o.is_closed, o.ingested_at, o.capture_contract_version AS contract
          FROM unnest(CAST(:symbols AS text[]), CAST(:times AS timestamptz[])) AS q(symbol, t)
          JOIN {table} o ON o.symbol = q.symbol AND o.time = q.t AND o.exchange = :exchange
           AND o.market_type = 'spot' AND o.timeframe = '1m'"""),
        {"symbols": [k[0] for k in keys], "times": [k[1] for k in keys], "exchange": exchange})
    return {(r["symbol"], r["minute"]): r for r in rows.mappings()}


async def run(args):
    from app.database import CeleryAsyncSessionLocal
    from app.ml.dataset_config import parse_required_ml_dataset_valid_from
    from app.ml.l3_managed_exit import definition, lane_config
    cutoff = datetime.fromisoformat(args.cutoff) if args.cutoff else datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        raise ValueError("--cutoff needs an explicit timezone")
    report = {"read_only": True, "cutoff": cutoff.isoformat()}
    per_trade, minute_rows = [], []
    async with CeleryAsyncSessionLocal() as db:
        await db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        await db.commit()
        owner = (await db.execute(text("""SELECT user_id::text, config_json FROM config_profiles
            WHERE config_type='ml' AND is_active = true LIMIT 1"""))).one()
        user_id = args.user_id or owner[0]
        cfg = lane_config(owner[1] if isinstance(owner[1], dict) else json.loads(owner[1]))
        spec = definition(cfg)
        if spec is None:
            raise ValueError("ml_l3_managed_exit is not configured")
        valid_from = parse_required_ml_dataset_valid_from(
            {"ml_dataset_valid_from": cfg.get("ml_l3_dataset_valid_from") or cfg["ml_dataset_valid_from"]})
        has_shadow_table = (await db.execute(text("SELECT to_regclass('public.ohlcv_shadow') IS NOT NULL"))).scalar()
        trades = await _population(db, user_id=user_id, spec_hash=spec["hash"], valid_from=valid_from,
                                   cutoff=cutoff, limit=args.limit)
        print(f"[gaps] {len(trades)} completed managed shadows", flush=True)
        for start in range(0, len(trades), args.batch_size):
            chunk = trades[start:start + args.batch_size]
            decisions = await _decisions(db, [t["id"] for t in chunk])
            pending = defaultdict(list)  # exchange -> [(trade, minute, skipping decision, prev decision)]
            for t in chunk:
                rows = decisions.get(t["id"], [])
                times = [r["candle_at"] for r in rows]
                by_time = {r["candle_at"]: r for r in rows}
                missing = gap_minutes(t["entry_timestamp"], times)
                prev_of = {}
                for i, r in enumerate(rows):
                    prev_of[r["candle_at"]] = rows[i - 1] if i else None
                per_trade.append({**{k: t[k] for k in ("id", "symbol", "outcome", "pnl_pct", "holding_seconds",
                                                       "persisted_reason", "persisted_valid")},
                                  "created_at": t["created_at"], "decisions": len(rows),
                                  "gap_minutes": len(missing),
                                  "gap_runs": len({skip for _, skip in missing})})
                exchange = "gate.io" if (t["exchange"] or "gate.io") in ("gate", "gateio", "gate.io") else t["exchange"]
                for minute, skip in missing:
                    pending[exchange].append((t, minute, by_time[skip], prev_of[skip]))
            for exchange, items in pending.items():
                keys = sorted({(t["symbol"], m) for t, m, _, _ in items})
                found = await _candles(db, exchange, keys, shadow_table=False)
                found_shadow = await _candles(db, exchange, keys, shadow_table=True) if has_shadow_table else {}
                for t, minute, skip, prev in items:
                    candle = found.get((t["symbol"], minute))
                    processed = _ts(skip["processed_at"])
                    if candle is None or candle["is_closed"] is not True or candle["ingested_at"] is None:
                        status = "MISSING_NOW" if candle is None else "NOT_CLOSED_OR_UNSTAMPED"
                    elif processed is not None and candle["ingested_at"] > processed:
                        status = "LATE_ARRIVAL"
                    else:
                        status = "PRESENT_IN_TIME" if processed is not None else "UNKNOWN_PROCESSED_AT"
                    stop = max(float(t["sl_price"]), float(prev["floor_price"])) if prev and prev["floor_price"] else float(t["sl_price"])
                    touch = None
                    if candle is not None:
                        touch = ("STOP_OR_FLOOR" if float(candle["low"]) <= stop else
                                 "TP" if float(candle["high"]) >= float(t["tp_price"]) else "NONE")
                    minute_rows.append({
                        "trade": t["id"], "symbol": t["symbol"], "outcome": t["outcome"], "minute": minute,
                        "status": status, "in_ohlcv_shadow": (t["symbol"], minute) in found_shadow,
                        "contract": candle["contract"] if candle else None,
                        "ingest_delay_s": (candle["ingested_at"] - (minute + MINUTE)).total_seconds()
                        if candle is not None and candle["ingested_at"] is not None else None,
                        "touch": touch})
            await db.commit()
            print(f"[gaps] {min(start + args.batch_size, len(trades))}/{len(trades)} scanned", flush=True)

    with_gaps = [t for t in per_trade if t["gap_minutes"]]
    def rate(rows, key):
        groups = defaultdict(lambda: [0, 0])
        for r in rows:
            g = groups[key(r)]
            g[0] += 1
            g[1] += bool(r["gap_minutes"])
        return {k: {"trades": v[0], "with_gap": v[1], "pct": round(100 * v[1] / v[0], 1)} for k, v in sorted(groups.items())}

    delays = sorted(r["ingest_delay_s"] for r in minute_rows if r["ingest_delay_s"] is not None)
    def q(p):
        return round(delays[min(len(delays) - 1, int(p * (len(delays) - 1)))], 1) if delays else None
    hours = defaultdict(lambda: {"gap_minutes": 0, "symbols": set(), "trades": set()})
    for r in minute_rows:
        h = hours[r["minute"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:00")]
        h["gap_minutes"] += 1; h["symbols"].add(r["symbol"]); h["trades"].add(r["trade"])
    top_hours = sorted(hours.items(), key=lambda kv: -kv[1]["gap_minutes"])[:25]
    report.update({
        "has_ohlcv_shadow_table": has_shadow_table,
        "trades": len(per_trade), "trades_with_gap": len(with_gaps),
        "persisted_reason_vs_gap": dict(Counter(f"{t['persisted_reason'] or 'VALID'}|{'gap' if t['gap_minutes'] else 'no_gap'}"
                                                for t in per_trade)),
        "gap_rate_by_outcome": rate(per_trade, lambda r: r["outcome"]),
        "gap_rate_by_holding": rate(per_trade, lambda r: _bucket((r["holding_seconds"] or 0) / 3600)),
        "gap_rate_by_day": rate(per_trade, lambda r: r["created_at"].astimezone(timezone.utc).date().isoformat()),
        "gap_minutes_total": len(minute_rows),
        "gap_minutes_per_gapped_trade": Counter(min(t["gap_minutes"], 20) for t in with_gaps).most_common(),
        "gap_runs_per_gapped_trade": Counter(min(t["gap_runs"], 20) for t in with_gaps).most_common(),
        "minute_status": dict(Counter(r["status"] for r in minute_rows)),
        "minute_status_by_in_ohlcv_shadow": dict(Counter(f"{r['status']}|shadow={r['in_ohlcv_shadow']}" for r in minute_rows)),
        "minute_contract": dict(Counter(str(r["contract"]) for r in minute_rows)),
        "ingest_delay_seconds_quantiles": {"p10": q(.1), "p50": q(.5), "p90": q(.9), "p99": q(.99), "max": q(1)},
        "missed_touch": dict(Counter(f"{r['outcome']}|{r['touch']}" for r in minute_rows)),
        "trades_with_missed_stop_touch": len({r["trade"] for r in minute_rows if r["touch"] == "STOP_OR_FLOOR"}),
        "trades_with_missed_tp_touch": len({r["trade"] for r in minute_rows if r["touch"] == "TP"}),
        "top_symbols": Counter(r["symbol"] for r in minute_rows).most_common(15),
        "top_hours_utc": [{"hour": k, "gap_minutes": v["gap_minutes"], "symbols": len(v["symbols"]),
                           "trades": len(v["trades"])} for k, v in top_hours],
    })
    payload = json.dumps(report, default=str, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload)
        if args.minutes_csv:
            import csv
            with open(args.minutes_csv, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(minute_rows[0]) if minute_rows else ["trade"])
                writer.writeheader()
                writer.writerows(minute_rows)
        print(f"[done] report written to {args.output}", flush=True)
    else:
        print(payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cutoff", help="UTC ISO timestamp (default: now)")
    parser.add_argument("--user-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--output")
    parser.add_argument("--minutes-csv", help="also write one row per missing minute")
    asyncio.run(run(parser.parse_args()))
