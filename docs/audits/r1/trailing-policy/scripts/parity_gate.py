"""Etapa 3 parity gate: reproduce recorded outcomes on Gate-final 1m candles,
using each shadow's own recorded trailing config. Also doubles as R1.A's
gravado-vs-reparado comparison.
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\backend")
from app.services.shadow_barrier_evaluator import evaluate_closed_candles

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
REPLAY_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"
OUT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\parity_results.json"

SHADOW_TRAILING_CONTRACT_VERSION = "shadow_hwm_trailing_v1"


def load_replay(symbol):
    path = f"{REPLAY_DIR}\\{symbol}.jsonl"
    candles = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            r["time"] = datetime.fromisoformat(r["time"])
            candles.append(r)
    candles.sort(key=lambda c: c["time"])
    return candles


def run_trailing_eval(row, candles, activation_pct, hwm_pct, never_sell_at_loss, protected_pct):
    entry_at = datetime.fromisoformat(row["entry_timestamp"])
    entry_bucket = entry_at.replace(second=0, microsecond=0)
    window = [c for c in candles if c["time"] >= entry_bucket]
    result = evaluate_closed_candles(
        window,
        entry_price=float(row["entry_price"]),
        entry_timestamp=entry_at,
        tp_price=float(row["tp_price"]),
        sl_price=float(row["sl_price"]),
        timeout_candles=int(row["timeout_candles"] or 1440),
        candles_seen_before=0,
        prior_high_water_mark=None,
        trailing_activation_profit_pct=activation_pct,
        trailing_hwm_pct=hwm_pct,
        trailing_never_sell_at_loss=never_sell_at_loss,
        trailing_protected_profit_pct=protected_pct,
    )
    return result


def main():
    rows = json.load(open(COHORT, encoding="utf-8"))
    replay_cache = {}

    results = []
    matched = 0
    mismatched = []
    outcome_recorded = Counter()
    outcome_reparado = Counter()

    for row in rows:
        sym = row["symbol"]
        if sym not in replay_cache:
            replay_cache[sym] = load_replay(sym)
        candles = replay_cache[sym]

        trailing = (row.get("config_snapshot") or {}).get("trailing") or {}
        enabled = trailing.get("enabled") is True and trailing.get("contract_version") == SHADOW_TRAILING_CONTRACT_VERSION
        activation_pct = float(trailing["activation_profit_pct"]) if enabled and trailing.get("activation_profit_pct") is not None else None
        hwm_pct = float(trailing["hwm_trail_pct"]) if enabled and trailing.get("hwm_trail_pct") is not None else None
        never_sell = bool(trailing.get("never_sell_at_loss"))
        protected_pct = max(float(trailing.get("min_profit_pct") or 0.0), float(trailing.get("safety_margin_above_entry_pct") or 0.0))

        result = run_trailing_eval(row, candles, activation_pct, hwm_pct, never_sell, protected_pct)

        recorded_outcome = row["outcome"]
        reparado_outcome = result.get("outcome")
        outcome_recorded[recorded_outcome] += 1
        outcome_reparado[reparado_outcome or "UNRESOLVED/PENDING"] += 1

        is_match = reparado_outcome == recorded_outcome
        if is_match:
            matched += 1
        else:
            mismatched.append({
                "id": row["id"],
                "symbol": sym,
                "entry_timestamp": row["entry_timestamp"],
                "recorded_outcome": recorded_outcome,
                "reparado_outcome": reparado_outcome,
                "recorded_exit": row.get("exit_price"),
                "reparado_exit_nominal": result.get("exit_price_nominal"),
                "reparado_exit_observed": result.get("exit_price_observed"),
                "recorded_barrier_touched_at": row.get("barrier_touched_at"),
                "reparado_barrier_touched_at": result.get("barrier_touched_at").isoformat() if result.get("barrier_touched_at") else None,
                "reason_code": result.get("reason_code"),
            })

        results.append({
            "id": row["id"],
            "symbol": sym,
            "recorded_outcome": recorded_outcome,
            "reparado_outcome": reparado_outcome,
            "match": is_match,
            "net_return_pct_recorded": row.get("net_return_pct"),
            "final_return_pct_recorded": row.get("final_return_pct"),
        })

    print("N =", len(rows))
    print("matched:", matched, f"({matched/len(rows)*100:.2f}%)")
    print("mismatched:", len(mismatched))
    print("outcome_recorded:", dict(outcome_recorded))
    print("outcome_reparado:", dict(outcome_reparado))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({
            "n": len(rows),
            "matched": matched,
            "mismatched_count": len(mismatched),
            "outcome_recorded": dict(outcome_recorded),
            "outcome_reparado": dict(outcome_reparado),
            "mismatched": mismatched,
            "all_results": results,
        }, f, indent=2, default=str)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
