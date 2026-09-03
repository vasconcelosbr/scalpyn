"""Read-only pull of the frozen 559 cohort from shadow_trades into a local cache."""
import json
import psycopg2
import psycopg2.extras

MANIFEST = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\r1a_cohort_559_manifest.json"
OUT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"

manifest = json.load(open(MANIFEST, encoding="utf-8"))
ids = manifest["ids"]
assert len(ids) == 559

conn = psycopg2.connect(
    "postgresql://postgres:pfVYvunFISWEeWAytUNApAAbxtsNcEHM@zephyr.proxy.rlwy.net:23422/railway"
)
conn.set_session(readonly=True)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute(
    """
    SELECT id, symbol, entry_price, entry_timestamp, tp_price, sl_price,
           outcome, exit_price, exit_price_nominal, exit_price_observed,
           exit_price_semantics, completed_at, barrier_touched_at,
           timeout_candles, config_snapshot, net_return_pct, final_return_pct,
           fee_roundtrip_pct_applied, mfe_pct, mae_pct, mae_at, mfe_at,
           features_snapshot_exit, profile_name, intrabar_convention,
           barrier_touched, exit_timestamp
    FROM shadow_trades
    WHERE id = ANY(%s::uuid[])
    """,
    (ids,),
)
rows = cur.fetchall()
print("rows fetched:", len(rows))

def default(o):
    return str(o)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(rows, f, default=default)

print("wrote", OUT)

# quick sanity
from collections import Counter
outcomes = Counter(r["outcome"] for r in rows)
print("outcome counts:", dict(outcomes))
symbols = sorted(set(r["symbol"] for r in rows))
print("n symbols:", len(symbols))
entry_times = [r["entry_timestamp"] for r in rows]
print("entry range:", min(entry_times), max(entry_times))
