import json, sys
from datetime import datetime
sys.path.insert(0, r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad")
from trailing_sim import simulate_policy, floor_fixed

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
REPLAY_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"
PARITY = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\parity_results.json"

SHADOW_TRAILING_CONTRACT_VERSION = "shadow_hwm_trailing_v1"

def load_replay(cache, symbol):
    if symbol in cache: return cache[symbol]
    candles=[]
    with open(f"{REPLAY_DIR}\{symbol}.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line); r["time"]=datetime.fromisoformat(r["time"]); candles.append(r)
    candles.sort(key=lambda c:c["time"])
    cache[symbol]=candles
    return candles

rows = json.load(open(COHORT, encoding="utf-8"))
parity = json.load(open(PARITY, encoding="utf-8"))
official_by_id = {r["id"]: r["reparado_outcome"] for r in parity["all_results"]}

cache={}
mismatches_vs_official=0
for row in rows:
    sym=row["symbol"]
    candles = load_replay(cache, sym)
    entry_at = datetime.fromisoformat(row["entry_timestamp"])
    entry_bucket = entry_at.replace(second=0, microsecond=0)
    window = [c for c in candles if c["time"] >= entry_bucket]

    trailing = (row.get("config_snapshot") or {}).get("trailing") or {}
    enabled = trailing.get("enabled") is True and trailing.get("contract_version")==SHADOW_TRAILING_CONTRACT_VERSION
    activation_pct = float(trailing["activation_profit_pct"]) if enabled and trailing.get("activation_profit_pct") is not None else 0
    trail_pct = float(trailing["hwm_trail_pct"]) if enabled and trailing.get("hwm_trail_pct") is not None else 0
    never_sell = bool(trailing.get("never_sell_at_loss"))
    protected_pct = max(float(trailing.get("min_profit_pct") or 0.0), float(trailing.get("safety_margin_above_entry_pct") or 0.0))

    def ffn(hwm, ep, a=activation_pct, t=trail_pct):
        if a<=0 or t<=0: return None
        return floor_fixed(hwm, ep, a, t)

    res = simulate_policy(
        window, entry_price=float(row["entry_price"]), entry_timestamp=entry_at,
        tp_price=float(row["tp_price"]), sl_price=float(row["sl_price"]),
        timeout_candles=int(row["timeout_candles"] or 1440),
        floor_fn=ffn, never_sell_at_loss=never_sell, protected_profit_pct=protected_pct,
    )
    my_outcome = res["outcome"]
    official_outcome = official_by_id[row["id"]]
    if my_outcome != official_outcome:
        mismatches_vs_official += 1
        print("MISMATCH", row["id"], sym, "official=",official_outcome, "mysim=",my_outcome)

print("total mismatches vs official evaluate_closed_candles:", mismatches_vs_official, "/", len(rows))
