"""Etapa 2: measure the cost of early exit, using the Gate-final replay path
and each shadow's own recorded exit boundary (barrier_touched_at)."""
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
REPLAY_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"
OUT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\etapa2_results.json"

TIMEOUT_HORIZON_MIN = 1440


def load_replay(cache, symbol):
    if symbol in cache:
        return cache[symbol]
    path = f"{REPLAY_DIR}\\{symbol}.jsonl"
    candles = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            r["time"] = datetime.fromisoformat(r["time"])
            candles.append(r)
    candles.sort(key=lambda c: c["time"])
    cache[symbol] = candles
    return candles


def bucket(diff_pp):
    d = max(diff_pp, 0.0)
    if d <= 0.5:
        return "<=0.5pp"
    if d <= 1.5:
        return "0.5-1.5pp"
    if d <= 3.0:
        return "1.5-3.0pp"
    return ">3.0pp"


def main():
    rows = json.load(open(COHORT, encoding="utf-8"))
    cache = {}
    by_outcome = defaultdict(list)

    for row in rows:
        outcome = row["outcome"]
        symbol = row["symbol"]
        entry_price = float(row["entry_price"])
        entry_at = datetime.fromisoformat(row["entry_timestamp"])
        entry_bucket_t = entry_at.replace(second=0, microsecond=0)
        boundary_raw = row.get("barrier_touched_at") or row.get("completed_at")
        if boundary_raw is None:
            continue
        boundary = datetime.fromisoformat(boundary_raw)
        horizon_end = entry_bucket_t + timedelta(minutes=TIMEOUT_HORIZON_MIN)

        candles = load_replay(cache, symbol)
        before = [c for c in candles if entry_bucket_t <= c["time"] <= boundary]
        after = [c for c in candles if boundary < c["time"] <= horizon_end]

        peak_before = max((c["high"] for c in before), default=entry_price)
        peak_before_pct = (peak_before / entry_price - 1) * 100

        realized_pct = float(row.get("final_return_pct")) if row.get("final_return_pct") is not None else (
            (float(row["exit_price"]) / entry_price - 1) * 100 if row.get("exit_price") else None
        )

        if after:
            max_after = max(c["high"] for c in after)
            max_after_time = next(c["time"] for c in after if c["high"] == max_after)
        else:
            max_after = None
            max_after_time = None
        max_after_pct = (max_after / entry_price - 1) * 100 if max_after is not None else None

        diff_pp = (max_after_pct - realized_pct) if (max_after_pct is not None and realized_pct is not None) else None
        minutes_to_max_after = (max_after_time - boundary).total_seconds() / 60 if max_after_time else None

        by_outcome[outcome].append({
            "id": row["id"],
            "symbol": symbol,
            "realized_pct": realized_pct,
            "peak_before_pct": peak_before_pct,
            "max_after_pct": max_after_pct,
            "diff_pp": diff_pp,
            "minutes_to_max_after": minutes_to_max_after,
            "n_candles_after": len(after),
        })

    summary = {}
    for outcome, items in by_outcome.items():
        buckets = defaultdict(lambda: {"n": 0, "sum_pp": 0.0})
        n_negative_or_zero = 0
        n_total = 0
        sum_diff_all = 0.0
        for it in items:
            if it["diff_pp"] is None:
                continue
            n_total += 1
            sum_diff_all += it["diff_pp"]
            if it["diff_pp"] <= 0:
                n_negative_or_zero += 1
            b = bucket(it["diff_pp"])
            buckets[b]["n"] += 1
            buckets[b]["sum_pp"] += max(it["diff_pp"], 0.0)
        summary[outcome] = {
            "n_total_with_horizon_data": n_total,
            "n_negative_or_zero_diff": n_negative_or_zero,
            "sum_diff_pp_signed": sum_diff_all,
            "avg_diff_pp_signed": sum_diff_all / n_total if n_total else None,
            "buckets": {k: {"n": v["n"], "pct_of_group": v["n"] / n_total * 100 if n_total else None, "sum_pp": v["sum_pp"]} for k, v in sorted(buckets.items())},
        }

    print(json.dumps(summary, indent=2, default=str))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "detail": {k: v for k, v in by_outcome.items()}}, f, indent=2, default=str)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
