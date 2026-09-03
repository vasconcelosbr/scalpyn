"""B.4: redo Etapa 3.5 without circularity -- recompute rsi_6/stoch_k AT
barrier_touched_at from the Gate-final 1m candles already reconstructed
for the frozen 559 cohort, using the exact production formulas (Wilder RSI,
stochastic k=14/d=3/smooth=3). entry_exhaustion_score has no known
producer/formula -- cannot be recomputed, reported as EVIDENCIA NAO
LOCALIZADA.
"""
import json
import random
import statistics
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
REPLAY_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"
TIMEOUT_HORIZON_MIN = 1440
random.seed(20260903)


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


def rsi_wilder(close: pd.Series, period: int) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 2) if pd.notna(val) else None


def stoch_k(df: pd.DataFrame, k_period=14, smooth=3) -> float | None:
    if len(df) < k_period + smooth:
        return None
    low_min = df["low"].rolling(window=k_period).min()
    high_max = df["high"].rolling(window=k_period).max()
    fast_k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    slow_k = fast_k.rolling(window=smooth).mean()
    val = slow_k.iloc[-1]
    return round(float(val), 2) if pd.notna(val) else None


rows = json.load(open(COHORT, encoding="utf-8"))
trailing_rows = [r for r in rows if r["outcome"] == "TRAILING_STOP"]
print("n trailing_stop:", len(trailing_rows))

cache = {}
usable = []
missing_touch = 0
insufficient_candles = 0

for r in trailing_rows:
    symbol = r["symbol"]
    entry_price = float(r["entry_price"])
    entry_at = datetime.fromisoformat(r["entry_timestamp"])
    entry_bucket = entry_at.replace(second=0, microsecond=0)
    boundary_raw = r.get("barrier_touched_at") or r.get("completed_at")
    if boundary_raw is None:
        missing_touch += 1
        continue
    boundary = datetime.fromisoformat(boundary_raw)

    candles = load_replay(cache, symbol)
    up_to_touch = [c for c in candles if c["time"] <= boundary]
    if len(up_to_touch) < 20:
        insufficient_candles += 1
        continue

    df = pd.DataFrame(up_to_touch)
    rsi6 = rsi_wilder(df["close"], 6)
    sk = stoch_k(df, 14, 3)
    if rsi6 is None or sk is None:
        insufficient_candles += 1
        continue

    horizon_end = entry_bucket + timedelta(minutes=TIMEOUT_HORIZON_MIN)
    after = [c for c in candles if boundary < c["time"] <= horizon_end]
    realized_pct = float(r.get("final_return_pct")) if r.get("final_return_pct") is not None else (
        (float(r["exit_price"]) / entry_price - 1) * 100 if r.get("exit_price") else None
    )
    if after:
        max_after = max(c["high"] for c in after)
        max_after_pct = (max_after / entry_price - 1) * 100
    else:
        max_after_pct = realized_pct
    diff_pp = max_after_pct - realized_pct

    usable.append({
        "id": r["id"], "symbol": symbol,
        "rsi_6_recomputed": rsi6, "stoch_k_recomputed": sk,
        "diff_pp": diff_pp,
    })

print("missing_touch:", missing_touch, "insufficient_candles:", insufficient_candles, "usable:", len(usable))

with open(r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\b4_results.json", "w", encoding="utf-8") as f:
    json.dump(usable, f, indent=2, default=str)


def auc(pos, neg, key):
    p = [x[key] for x in pos]
    n = [x[key] for x in neg]
    count = 0
    total = len(p) * len(n)
    for a in p:
        for b in n:
            if a > b:
                count += 1
            elif a == b:
                count += 0.5
    return count / total if total else None


cont = [u for u in usable if u["diff_pp"] > 0.5]
rev = [u for u in usable if u["diff_pp"] <= 0.5]
print("continued(>0.5pp) n=", len(cont), " else n=", len(rev))

for label, key in [("rsi_6_recomputed", "rsi_6_recomputed"), ("stoch_k_recomputed", "stoch_k_recomputed")]:
    a = auc(cont, rev, key)
    c_vals = [x[key] for x in cont]
    r_vals = [x[key] for x in rev]
    print(f"{label}: AUC={a:.4f}" if a is not None else f"{label}: AUC=None",
          "cont_median=", statistics.median(c_vals) if c_vals else None,
          "rev_median=", statistics.median(r_vals) if r_vals else None)

    boots = []
    for _ in range(3000):
        bp = [cont[random.randrange(len(cont))] for _ in range(len(cont))]
        bn = [rev[random.randrange(len(rev))] for _ in range(len(rev))]
        boots.append(auc(bp, bn, key))
    boots.sort()
    lo = boots[int(0.025 * len(boots))]
    hi = boots[int(0.975 * len(boots))]
    print(f"  CI95=[{lo:.4f}, {hi:.4f}]")
