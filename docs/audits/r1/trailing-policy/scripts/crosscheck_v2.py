import sys, json
from datetime import datetime
sys.path.insert(0, r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad")
sys.path.insert(0, r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\backend")
from trailing_sim import simulate_policy, floor_fixed, floor_proportional, floor_stepped
from app.services.shadow_barrier_evaluator import evaluate_closed_candles_policy_v2

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
REPLAY_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"


def load_replay(cache, symbol):
    if symbol in cache:
        return cache[symbol]
    candles = []
    with open(REPLAY_DIR + "\\" + symbol + ".jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            r["time"] = datetime.fromisoformat(r["time"])
            candles.append(r)
    candles.sort(key=lambda c: c["time"])
    cache[symbol] = candles
    return candles


rows = json.load(open(COHORT, encoding="utf-8"))
cache = {}

policies = [
    ("FIXED", {"policy_family": "FIXED", "activation_profit_pct": 0.8, "hwm_trail_pct": 0.15},
     lambda hwm, ep: floor_fixed(hwm, ep, 0.8, 0.15)),
    ("PROPORTIONAL", {"policy_family": "PROPORTIONAL", "k": 0.30},
     lambda hwm, ep: floor_proportional(hwm, ep, 0.30)),
    ("STEPPED", {"policy_family": "STEPPED",
                 "steps": [{"peak_profit_pct": 2.0, "floor_profit_pct": 1.5},
                           {"peak_profit_pct": 4.0, "floor_profit_pct": 2.5}],
                 "base_activation_profit_pct": None, "base_hwm_trail_pct": None},
     lambda hwm, ep: floor_stepped(hwm, ep, [(2.0, 1.5), (4.0, 2.5)], None)),
]

for name, tp_dict, fn in policies:
    mismatches = 0
    for row in rows:
        sym = row["symbol"]
        candles = load_replay(cache, sym)
        entry_at = datetime.fromisoformat(row["entry_timestamp"])
        eb = entry_at.replace(second=0, microsecond=0)
        window = [c for c in candles if c["time"] >= eb]
        trailing = (row.get("config_snapshot") or {}).get("trailing") or {}
        never_sell = bool(trailing.get("never_sell_at_loss"))
        protected_pct = max(float(trailing.get("min_profit_pct") or 0.0), float(trailing.get("safety_margin_above_entry_pct") or 0.0))

        r1 = simulate_policy(window, entry_price=float(row["entry_price"]), entry_timestamp=entry_at,
                              tp_price=float(row["tp_price"]), sl_price=float(row["sl_price"]),
                              timeout_candles=int(row["timeout_candles"] or 1440),
                              floor_fn=fn, never_sell_at_loss=never_sell, protected_profit_pct=protected_pct)

        r2 = evaluate_closed_candles_policy_v2(window, entry_price=float(row["entry_price"]), entry_timestamp=entry_at,
                                                tp_price=float(row["tp_price"]), sl_price=float(row["sl_price"]),
                                                timeout_candles=int(row["timeout_candles"] or 1440),
                                                trailing_policy=tp_dict, trailing_never_sell_at_loss=never_sell,
                                                trailing_protected_profit_pct=protected_pct)

        if r1["outcome"] != r2["outcome"] or r1.get("exit_price_nominal") != r2.get("exit_price_nominal"):
            mismatches += 1
            if mismatches <= 3:
                print("MISMATCH", name, row["id"], r1["outcome"], r1.get("exit_price_nominal"),
                      "vs", r2["outcome"], r2.get("exit_price_nominal"))
    print(name, "mismatches out of", len(rows), ":", mismatches)
