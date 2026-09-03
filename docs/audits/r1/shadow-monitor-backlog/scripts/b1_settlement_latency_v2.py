import psycopg2
from collections import defaultdict

conn = psycopg2.connect(
    "postgresql://postgres:pfVYvunFISWEeWAytUNApAAbxtsNcEHM@zephyr.proxy.rlwy.net:23422/railway"
)
conn.set_session(readonly=True)
cur = conn.cursor()

cur.execute("""
    SELECT symbol, timeframe, candle_open_time, delay_target_seconds,
           found, open, high, low, close, volume, quote_volume
    FROM ohlcv_settlement_latency_samples
    WHERE found = TRUE
    ORDER BY symbol, timeframe, candle_open_time, delay_target_seconds
""")
rows = cur.fetchall()
print("total found=TRUE rows:", len(rows))

FIELDS = ["open", "high", "low", "close", "volume", "quote_volume"]
groups = defaultdict(dict)
for r in rows:
    symbol, timeframe, open_time, delay, found, *vals = r
    groups[(symbol, timeframe, open_time)][delay] = dict(zip(FIELDS, vals))

n_samples_hist = defaultdict(int)
for key, by_delay in groups.items():
    n_samples_hist[len(by_delay)] += 1
print("groups by sample-count:", dict(sorted(n_samples_hist.items())))

usable = {k: v for k, v in groups.items() if len(v) >= 2}
print("usable groups (>=2 delays present):", len(usable))


def pct(sorted_list, p):
    if not sorted_list:
        return None
    idx = min(len(sorted_list) - 1, int(round(p * (len(sorted_list) - 1))))
    return sorted_list[idx]


# For each usable group: "final" = value at the LARGEST available delay.
# Stabilization for a field = smallest available delay whose value already
# equals that final value. This is a lower-bound proxy given incomplete
# per-candle coverage (max delay available per candle is often < 300s).
stab_by_field = defaultdict(list)
stab_by_field_symbol = defaultdict(list)
stab_by_field_tf = defaultdict(list)
max_delay_seen = []
changed_between_first_and_last = defaultdict(int)
total_pairs = defaultdict(int)

for key, by_delay in usable.items():
    symbol, timeframe, _ = key
    delays_sorted = sorted(by_delay)
    max_delay_seen.append(delays_sorted[-1])
    final = by_delay[delays_sorted[-1]]
    first = by_delay[delays_sorted[0]]
    for field in FIELDS:
        total_pairs[field] += 1
        if first[field] != final[field]:
            changed_between_first_and_last[field] += 1
        final_val = final[field]
        stab_delay = delays_sorted[-1]
        for d in delays_sorted:
            if by_delay[d][field] == final_val:
                stab_delay = d
                break
        stab_by_field[field].append(stab_delay)
        stab_by_field_symbol[(field, symbol)].append(stab_delay)
        stab_by_field_tf[(field, timeframe)].append(stab_delay)

print("\nmax delay available per group -- distribution:")
mh = defaultdict(int)
for d in max_delay_seen:
    mh[d] += 1
print(dict(sorted(mh.items())))

print("\n=== Any change between first- and last-available sample, by field ===")
for field in FIELDS:
    n = total_pairs[field]
    c = changed_between_first_and_last[field]
    print(f"{field:14s} n_pairs={n:5d} changed={c:4d} ({c/n*100:.2f}%)")

print("\n=== Stabilization delay proxy by field ===")
for field in FIELDS:
    vals = sorted(stab_by_field[field])
    if not vals:
        continue
    print(f"{field:14s} n={len(vals):5d} p50={pct(vals,0.5):4d} p90={pct(vals,0.9):4d} p99={pct(vals,0.99):4d} max={max(vals):4d}")

print("\n=== Stabilization delay proxy by (close, symbol) ===")
for symbol in ("BTC_USDT", "LINK_USDT", "NEAR_USDT", "SOL_USDT", "TAO_USDT", "XDC_USDT"):
    vals = sorted(stab_by_field_symbol[("close", symbol)])
    if not vals:
        continue
    print(f"{symbol:12s} n={len(vals):5d} p50={pct(vals,0.5):4d} p90={pct(vals,0.9):4d} p99={pct(vals,0.99):4d} max={max(vals):4d}")

print("\n=== Stabilization delay proxy by (close, timeframe) ===")
for tf in ("1m", "5m", "30m"):
    vals = sorted(stab_by_field_tf[("close", tf)])
    if not vals:
        continue
    print(f"{tf:6s} n={len(vals):5d} p50={pct(vals,0.5):4d} p90={pct(vals,0.9):4d} p99={pct(vals,0.99):4d} max={max(vals):4d}")

# Worst-field-per-candle, only for groups whose max available delay is 300
# (so we have a genuine "did it ever change by the true 300s ceiling" read)
full_ceiling = {k: v for k, v in usable.items() if 300 in v and len(v) >= 2}
print("\ngroups reaching the 300s sample specifically (>=2 delays, one of them 300):", len(full_ceiling))
worst = []
for key, by_delay in full_ceiling.items():
    delays_sorted = sorted(by_delay)
    final = by_delay[300]
    w = delays_sorted[0]
    for field in FIELDS:
        final_val = final[field]
        for d in delays_sorted:
            if by_delay[d][field] == final_val:
                w = max(w, d)
                break
    worst.append(w)
worst.sort()
if worst:
    print(f"n={len(worst)} p50={pct(worst,0.5)} p90={pct(worst,0.9)} p99={pct(worst,0.99)} max={max(worst)}")
    n_over_60 = sum(1 for v in worst if v > 60)
    print(f"n(worst-field stab proxy >60s)={n_over_60} ({n_over_60/len(worst)*100:.3f}%)")
