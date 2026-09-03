import json, sys, statistics
from datetime import datetime
sys.path.insert(0, r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad")
from trailing_sim import simulate_policy, floor_fixed

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
REPLAY_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"
FEE = 0.2

def load_replay(cache, symbol):
    if symbol in cache: return cache[symbol]
    candles=[]
    with open(REPLAY_DIR + "\\" + symbol + ".jsonl", encoding="utf-8") as f:
        for line in f:
            r=json.loads(line); r["time"]=datetime.fromisoformat(r["time"]); candles.append(r)
    candles.sort(key=lambda c:c["time"]); cache[symbol]=candles
    return candles

rows = json.load(open(COHORT, encoding="utf-8"))
cache={}
pre={}
for row in rows:
    sym=row["symbol"]; candles=load_replay(cache, sym)
    entry_at=datetime.fromisoformat(row["entry_timestamp"]); eb=entry_at.replace(second=0,microsecond=0)
    window=[c for c in candles if c["time"]>=eb]
    trailing=(row.get("config_snapshot") or {}).get("trailing") or {}
    never_sell=bool(trailing.get("never_sell_at_loss"))
    protected_pct=max(float(trailing.get("min_profit_pct") or 0.0), float(trailing.get("safety_margin_above_entry_pct") or 0.0))
    pre[row["id"]]=dict(entry_price=float(row["entry_price"]), window=window, tp_price=float(row["tp_price"]),
        sl_price=float(row["sl_price"]), timeout_candles=int(row["timeout_candles"] or 1440),
        never_sell=never_sell, protected_pct=protected_pct, entry_at=entry_at)

def run(activation, trail):
    nets=[]
    for row in rows:
        t=pre[row["id"]]
        fn=lambda hwm,ep,a=activation,tr=trail: floor_fixed(hwm,ep,a,tr)
        res=simulate_policy(t["window"], entry_price=t["entry_price"], entry_timestamp=t["entry_at"],
            tp_price=t["tp_price"], sl_price=t["sl_price"], timeout_candles=t["timeout_candles"],
            floor_fn=fn, never_sell_at_loss=t["never_sell"], protected_profit_pct=t["protected_pct"])
        exit_nom=res["exit_price_nominal"]
        if exit_nom is None: continue
        gross=(exit_nom/t["entry_price"]-1)*100
        nets.append(gross-FEE)
    return statistics.mean(nets), sum(nets), len(nets)

for activation in (0.65,0.7,0.75,0.8,0.85,0.9,0.95):
    for trail in (0.12,0.13,0.14,0.15,0.16,0.17,0.18):
        m, s, n = run(activation, trail)
        print(f"act={activation} trail={trail} net_exp={m:.5f} sum={s:.2f} n={n}")
