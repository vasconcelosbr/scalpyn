import json, statistics
import psycopg2, psycopg2.extras
from datetime import datetime, timedelta

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
REPLAY_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"

def load_replay(cache, symbol):
    if symbol in cache: return cache[symbol]
    candles=[]
    with open(REPLAY_DIR + "\\" + symbol + ".jsonl", encoding="utf-8") as f:
        for line in f:
            r=json.loads(line); r["time"]=datetime.fromisoformat(r["time"]); candles.append(r)
    candles.sort(key=lambda c:c["time"]); cache[symbol]=candles
    return candles

rows = json.load(open(COHORT, encoding="utf-8"))
trailing_rows = [r for r in rows if r["outcome"]=="TRAILING_STOP"]
print("n trailing_stop:", len(trailing_rows))

capture_failed = 0
missing_score = 0
usable = []
cache={}
for r in trailing_rows:
    fse = r.get("features_snapshot_exit")
    if not fse or fse.get("_capture_failed"):
        capture_failed += 1
        continue
    score = fse.get("entry_exhaustion_score")
    rsi6 = fse.get("rsi_6")
    stoch_k = fse.get("stoch_k")
    if score is None or rsi6 is None or stoch_k is None:
        missing_score += 1
        continue

    symbol = r["symbol"]
    candles = load_replay(cache, symbol)
    entry_price = float(r["entry_price"])
    entry_at = datetime.fromisoformat(r["entry_timestamp"])
    entry_bucket = entry_at.replace(second=0, microsecond=0)
    boundary_raw = r.get("barrier_touched_at") or r.get("completed_at")
    boundary = datetime.fromisoformat(boundary_raw)
    horizon_end = entry_bucket + timedelta(minutes=1440)
    after = [c for c in candles if boundary < c["time"] <= horizon_end]
    if r.get("final_return_pct") is not None:
        realized_pct = float(r.get("final_return_pct"))
    elif r.get("exit_price") is not None:
        realized_pct = (float(r["exit_price"]) / entry_price - 1) * 100
    else:
        missing_score += 1
        continue
    if after:
        max_after = max(c["high"] for c in after)
        max_after_pct = (max_after/entry_price - 1)*100
    else:
        max_after_pct = realized_pct
    continued = max_after_pct > realized_pct  # price kept making new highs after exit vs reverted

    usable.append({
        "id": r["id"], "score": score, "rsi_6": rsi6, "stoch_k": stoch_k,
        "realized_pct": realized_pct, "max_after_pct": max_after_pct,
        "diff_pp": max_after_pct - realized_pct, "continued": continued,
    })

print("capture_failed:", capture_failed, "missing_score:", missing_score, "usable:", len(usable))

# Threshold for continued/reverted: use diff_pp > 0.5pp as "continued materially", else "reverted/flat"
# report both a >0 threshold and a >0.5pp threshold
def describe(vals):
    if not vals: return None
    s = sorted(vals)
    n = len(s)
    def pct(p):
        idx = min(n-1, max(0, int(round(p*(n-1)))))
        return s[idx]
    return {"n": n, "mean": statistics.mean(vals), "median": statistics.median(vals), "p25": pct(0.25), "p75": pct(0.75)}

def auc(pos, neg):
    # Mann-Whitney U based AUC: P(score_pos > score_neg)
    if not pos or not neg: return None
    count = 0
    total = len(pos)*len(neg)
    for p in pos:
        for ng in neg:
            if p > ng: count += 1
            elif p == ng: count += 0.5
    return count/total

for label, key in [("entry_exhaustion_score","score"), ("rsi_6","rsi_6"), ("stoch_k","stoch_k")]:
    cont = [u[key] for u in usable if u["continued"]]
    rev = [u[key] for u in usable if not u["continued"]]
    print(f"--- {label} --- continued(n={len(cont)}):", describe(cont), " reverted(n={}):".format(len(rev)), describe(rev))
    a = auc(cont, rev)
    print(f"AUC({label}, continued>reverted) = {a}")

# also threshold diff_pp > 0.5pp as "materially continued"
cont2 = [u for u in usable if u["diff_pp"] > 0.5]
rev2 = [u for u in usable if u["diff_pp"] <= 0.5]
print("materially-continued (>0.5pp) n=", len(cont2), " else n=", len(rev2))
for label, key in [("entry_exhaustion_score","score"), ("rsi_6","rsi_6"), ("stoch_k","stoch_k")]:
    cont = [u[key] for u in cont2]
    rev = [u[key] for u in rev2]
    a = auc(cont, rev)
    print(f"AUC({label}, materially-continued threshold) = {a}  continued_desc={describe(cont)} else_desc={describe(rev)}")

with open(r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\etapa35_results.json", "w", encoding="utf-8") as f:
    json.dump({"usable": usable, "capture_failed": capture_failed, "missing_score": missing_score}, f, indent=2, default=str)
