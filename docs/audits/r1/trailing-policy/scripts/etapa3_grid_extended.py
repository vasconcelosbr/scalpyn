import json
import sys
import statistics
from datetime import datetime
sys.path.insert(0, r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad")
from trailing_sim import simulate_policy, floor_fixed, floor_proportional

COHORT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\cohort_rows.json"
REPLAY_DIR = r"C:\Users\ricar\codex-worktrees\scalpyn-correcoes-sequenciadas-20260902\docs\audits\r1\trailing-policy\replay_1m"
OUT = r"C:\Users\ricar\AppData\Local\Temp\claude\C--WINDOWS-system32\8739466e-e897-4b8b-a08a-d8c741a5df75\scratchpad\etapa3_grid_extended_results.json"

FEE_ROUNDTRIP_PCT = 0.2


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


def build_policies():
    policies = {}
    for activation in (0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
        for trail in (0.10, 0.15, 0.20, 0.25, 0.35):
            name = f"P1x_FIXED_act{activation}_trail{trail}"
            policies[name] = {
                "family": "FIXED",
                "params": {"activation_profit_pct": activation, "hwm_trail_pct": trail},
                "fn": (lambda hwm, ep, a=activation, t=trail: floor_fixed(hwm, ep, a, t)),
            }
    for k in (0.05, 0.10, 0.15, 0.20):
        name = f"P3x_PROPORTIONAL_k{k}"
        policies[name] = {
            "family": "PROPORTIONAL",
            "params": {"k": k},
            "fn": (lambda hwm, ep, kk=k: floor_proportional(hwm, ep, kk)),
        }
    return policies


def main():
    rows = json.load(open(COHORT, encoding="utf-8"))
    cache = {}
    policies = build_policies()

    per_trade_cache = {}
    for row in rows:
        sym = row["symbol"]
        candles = load_replay(cache, sym)
        entry_at = datetime.fromisoformat(row["entry_timestamp"])
        entry_bucket = entry_at.replace(second=0, microsecond=0)
        window = [c for c in candles if c["time"] >= entry_bucket]
        trailing = (row.get("config_snapshot") or {}).get("trailing") or {}
        never_sell = bool(trailing.get("never_sell_at_loss"))
        protected_pct = max(float(trailing.get("min_profit_pct") or 0.0), float(trailing.get("safety_margin_above_entry_pct") or 0.0))
        per_trade_cache[row["id"]] = dict(
            entry_price=float(row["entry_price"]), window=window,
            tp_price=float(row["tp_price"]), sl_price=float(row["sl_price"]),
            timeout_candles=int(row["timeout_candles"] or 1440),
            never_sell=never_sell, protected_pct=protected_pct,
            entry_at=entry_at, symbol=sym, recorded_outcome=row["outcome"],
        )

    cell_results = {}
    for pname, pdef in policies.items():
        fn = pdef["fn"]
        trades = []
        for row in rows:
            t = per_trade_cache[row["id"]]
            res = simulate_policy(
                t["window"], entry_price=t["entry_price"], entry_timestamp=t["entry_at"],
                tp_price=t["tp_price"], sl_price=t["sl_price"], timeout_candles=t["timeout_candles"],
                floor_fn=fn, never_sell_at_loss=t["never_sell"], protected_profit_pct=t["protected_pct"],
            )
            outcome = res["outcome"]
            exit_nom = res["exit_price_nominal"]
            gross_pct = (exit_nom / t["entry_price"] - 1) * 100 if exit_nom is not None else None
            net_pct = (gross_pct - FEE_ROUNDTRIP_PCT) if gross_pct is not None else None
            touched_at = res["barrier_touched_at"]
            duration_min = (touched_at - t["entry_at"]).total_seconds() / 60 if touched_at else None
            trades.append({
                "id": row["id"], "symbol": t["symbol"], "outcome": outcome,
                "gross_pct": gross_pct, "net_pct": net_pct, "duration_min": duration_min,
                "recorded_outcome": t["recorded_outcome"],
                "entry_minute": t["entry_at"].replace(second=0, microsecond=0).isoformat(),
            })

        outcomes_ct = {}
        for tr in trades:
            outcomes_ct[tr["outcome"] or "UNRESOLVED"] = outcomes_ct.get(tr["outcome"] or "UNRESOLVED", 0) + 1
        nets = [tr["net_pct"] for tr in trades if tr["net_pct"] is not None]
        grosses = [tr["gross_pct"] for tr in trades if tr["gross_pct"] is not None]
        durations = [tr["duration_min"] for tr in trades if tr["duration_min"] is not None]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
        changed_vs_recorded = sum(1 for tr in trades if tr["outcome"] != tr["recorded_outcome"])

        cell_results[pname] = {
            "family": policies[pname]["family"], "params": policies[pname]["params"],
            "n": len(trades), "outcome_composition": outcomes_ct,
            "gross_expectancy_pct": statistics.mean(grosses) if grosses else None,
            "net_expectancy_pct": statistics.mean(nets) if nets else None,
            "net_sum_pp": sum(nets) if nets else None,
            "net_median_pct": statistics.median(nets) if nets else None,
            "net_stdev_pct": statistics.pstdev(nets) if len(nets) > 1 else None,
            "profit_factor_net": profit_factor,
            "win_rate_pct": (len(wins) / len(nets) * 100) if nets else None,
            "avg_duration_min": statistics.mean(durations) if durations else None,
            "n_changed_outcome_vs_recorded": changed_vs_recorded,
            "trades": trades,
        }
        print(pname, "net_exp=", round(cell_results[pname]["net_expectancy_pct"], 5),
              "sum=", round(cell_results[pname]["net_sum_pp"], 2),
              "wr=", round(cell_results[pname]["win_rate_pct"], 2),
              "pf=", round(profit_factor, 4) if profit_factor else None,
              "changed=", changed_vs_recorded)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cell_results, f, indent=2, default=str)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
