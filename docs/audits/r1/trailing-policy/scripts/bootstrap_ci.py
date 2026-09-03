import json, sys, random, statistics
from datetime import datetime
from collections import defaultdict
sys.path.insert(0, r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad")
from trailing_sim import simulate_policy, floor_fixed

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
REPLAY_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"
FEE = 0.2
random.seed(20260903)

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
        never_sell=never_sell, protected_pct=protected_pct, entry_at=entry_at, symbol=sym,
        cluster=(sym, eb.isoformat()))

def net_pct_series(activation, trail):
    out = {}
    for row in rows:
        t=pre[row["id"]]
        fn=lambda hwm,ep,a=activation,tr=trail: floor_fixed(hwm,ep,a,tr)
        res=simulate_policy(t["window"], entry_price=t["entry_price"], entry_timestamp=t["entry_at"],
            tp_price=t["tp_price"], sl_price=t["sl_price"], timeout_candles=t["timeout_candles"],
            floor_fn=fn, never_sell_at_loss=t["never_sell"], protected_profit_pct=t["protected_pct"])
        exit_nom=res["exit_price_nominal"]
        gross=(exit_nom/t["entry_price"]-1)*100 if exit_nom is not None else 0.0
        out[row["id"]] = gross - FEE
    return out

vigente = net_pct_series(1.0, 0.35)
candidate = net_pct_series(0.8, 0.25)
candidate_tight = net_pct_series(0.8, 0.15)

# cluster map: cluster -> list of trade ids
clusters = defaultdict(list)
for row in rows:
    clusters[pre[row["id"]]["cluster"]].append(row["id"])
cluster_keys = list(clusters.keys())
print("n_clusters:", len(cluster_keys), "n_trades:", len(rows))

def diff_series(a, b):
    return {i: a[i]-b[i] for i in a}

def cluster_bootstrap_mean_ci(diffmap, n_boot=5000):
    # resample clusters with replacement, pool their trade-level diffs, compute mean
    means = []
    for _ in range(n_boot):
        sampled_ids = []
        for _ in range(len(cluster_keys)):
            k = cluster_keys[random.randrange(len(cluster_keys))]
            sampled_ids.extend(clusters[k])
        means.append(statistics.mean(diffmap[i] for i in sampled_ids))
    means.sort()
    lo = means[int(0.025*n_boot)]
    hi = means[int(0.975*n_boot)]
    return statistics.mean(means), lo, hi

for name, cand in [("act0.8_trail0.25_vs_vigente", candidate), ("act0.8_trail0.15_vs_vigente", candidate_tight)]:
    d = diff_series(cand, vigente)
    point = statistics.mean(d.values())
    boot_mean, lo, hi = cluster_bootstrap_mean_ci(d)
    print(f"{name}: point_diff={point:.5f} boot_mean={boot_mean:.5f} CI95=[{lo:.5f}, {hi:.5f}] includes_zero={lo<=0<=hi}")
