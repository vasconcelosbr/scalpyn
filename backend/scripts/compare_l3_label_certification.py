"""Read-only comparison of L3 managed-label certification rules (v1 x v2).

Nothing is written: the session is READ ONLY, proofs live in memory, models are
never persisted. It answers two questions on the SAME population and cutoff:

1. Census -- which completed L3 managed shadows does each rule set certify,
   split by outcome / holding time, and what is the net EV of each population
   versus the full (uncensored) population?
2. Training -- the official v1 trainer population (persisted proofs) and the
   v2 population (in-memory proofs) are fitted with the same trainer helpers,
   config, seed and split policy, then evaluated on their own holdouts.

PowerShell (backend/ as working directory, DATABASE_URL pointing to prod):
  python -m scripts.compare_l3_label_certification `
      --flow-evidence REPLAY_FALLBACK --timeout-horizon POLICY_TIMEOUT `
      --output l3_cert_compare.json
"""
import argparse
import asyncio
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import text

HOLDING_BUCKETS_H = (0.5, 1, 2, 4, 8, 16, 24.5)


def _net(row, fee):
    return float(row["pnl_pct"]) - fee


def _summary(rows, fee):
    if not rows:
        return {"n": 0}
    net = [_net(r, fee) for r in rows]
    n = len(net)
    mean = sum(net) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in net) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "net_ev_pct": round(mean, 4), "naive_ci95_halfwidth": round(1.96 * sd / math.sqrt(n), 4) if n > 1 else None,
            "positive_rate": round(sum(x > 0 for x in net) / n, 4),
            "median_holding_h": round(sorted(r["holding_seconds"] or 0 for r in rows)[n // 2] / 3600, 3)}


def _bucket(hours):
    for edge in HOLDING_BUCKETS_H:
        if hours < edge:
            return f"<{edge}h"
    return f">={HOLDING_BUCKETS_H[-1]}h"


async def _load_evidence_batch(db, chunk):
    """Same predicates as l3_managed_exit.load_certification_evidence (compact),
    fetched for many shadows per round trip (the census may run over a public
    proxy). The per-shadow loader is kept as --batch-size 1 for parity checks."""
    ids = [str(r["id"]) for r in chunk]
    decisions = defaultdict(list)
    result = await db.execute(text("""SELECT shadow_id::text AS shadow_id, candle_at, available_at, policy_hash,
            evidence - 'flow_buckets' AS evidence, state
        FROM shadow_l3_exit_decisions WHERE shadow_id = ANY(CAST(:ids AS uuid[]))
        ORDER BY shadow_id, candle_at"""), {"ids": ids})
    for row in result.mappings():
        decisions[row["shadow_id"]].append({k: row[k] for k in ("candle_at", "available_at", "policy_hash", "evidence", "state")})
    boundaries = {}
    by_exchange = defaultdict(list)
    for r in chunk:
        exchange = "gate.io" if r["exchange"] in ("gate", "gateio", "gate.io") else r["exchange"]
        by_exchange[exchange].append(r)
    for exchange, rows in by_exchange.items():
        result = await db.execute(text("""SELECT q.id, o.time, o.high, o.low
            FROM unnest(CAST(:ids AS text[]), CAST(:symbols AS text[]), CAST(:entries AS timestamptz[])) AS q(id, symbol, entry)
            JOIN ohlcv o ON o.symbol = q.symbol AND o.exchange = :exchange AND o.market_type = 'spot'
             AND o.timeframe = '1m' AND o.is_closed IS TRUE AND o.time = date_trunc('minute', q.entry)"""),
            {"ids": [str(r["id"]) for r in rows], "symbols": [r["symbol"] for r in rows],
             "entries": [r["entry_timestamp"] for r in rows], "exchange": exchange})
        for row in result.mappings():
            boundaries.setdefault(row["id"], {"time": row["time"], "high": row["high"], "low": row["low"]})
    return {sid: (decisions.get(sid, []), boundaries.get(sid)) for sid in ids}


async def _census(db, *, user_id, spec, valid_from, cutoff, rules_v2, fee, limit, batch_size):
    from app.ml.l3_managed_exit import VERSION, certify, load_certification_evidence
    rows = (await db.execute(text(f"""
        SELECT st.id, st.symbol, st.exchange, st.config_snapshot, st.label_contract_version, st.lineage_status,
               st.source, st.entry_timestamp, st.exit_timestamp, st.outcome, st.closure_path, st.entry_price,
               st.tp_price, st.sl_price, st.timeout_candles, st.exit_price, st.exit_price_semantics, st.pnl_pct,
               st.holding_seconds, st.created_at, st.completed_at, st.profile_id::text AS profile_id,
               sx.state->'ml_label' AS persisted_label
          FROM shadow_trades st
          LEFT JOIN shadow_l3_exit_states sx ON sx.shadow_id = st.id
         WHERE st.user_id = :uid AND st.source = 'L3' AND st.label_contract_version = :version
           AND st.config_snapshot->'l3_managed_ml'->'contract'->>'hash' = :hash
           AND st.entry_timestamp >= :valid_from AND st.outcome IS NOT NULL
           AND st.pnl_pct IS NOT NULL AND st.completed_at <= :cutoff
         ORDER BY st.created_at {('LIMIT ' + str(int(limit))) if limit else ''}
    """), {"uid": str(user_id), "version": VERSION, "hash": spec["hash"], "valid_from": valid_from,
           "cutoff": cutoff})).mappings().all()
    proofs_v1, proofs_v2, out = {}, {}, []
    parity = Counter()
    print(f"[census] {len(rows)} completed managed shadows", flush=True)
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        evidence = await _load_evidence_batch(db, chunk) if batch_size > 1 else {
            str(r["id"]): await load_certification_evidence(db, SimpleNamespace(**r), compact=True) for r in chunk}
        for row in chunk:
            shadow = SimpleNamespace(**row)
            decisions, boundary = evidence[str(row["id"])]
            p1 = certify(shadow, decisions, boundary, checked_at=cutoff)
            p2 = certify(shadow, decisions, boundary, checked_at=cutoff, certification=rules_v2)
            sid = str(row["id"])
            proofs_v1[sid], proofs_v2[sid] = p1, p2
            persisted = row["persisted_label"] or {}
            parity["match" if bool(persisted.get("valid")) == p1["valid"] else
                   ("persisted_missing" if not persisted else "mismatch")] += 1
            out.append({**dict(row), "v1": p1, "v2": p2})
        await db.commit()  # read-only session: only ends the MVCC snapshot
        print(f"[census] {min(start + batch_size, len(rows))}/{len(rows)} certified", flush=True)

    def by(key_fn):
        groups = defaultdict(list)
        for r in out:
            groups[key_fn(r)].append(r)
        return {k: {"all": _summary(v, fee), "v1_valid": _summary([r for r in v if r["v1"]["valid"]], fee),
                    "v2_valid": _summary([r for r in v if r["v2"]["valid"]], fee)} for k, v in sorted(groups.items())}

    census = {
        "population": len(out),
        "v1_recomputed_vs_persisted_validity": dict(parity),
        "overall": by(lambda r: "ALL")["ALL"],
        "by_outcome": by(lambda r: r["outcome"]),
        "by_holding": by(lambda r: _bucket((r["holding_seconds"] or 0) / 3600)),
        "by_day": {k: {"all": v["all"]["n"], "v1": v["v1_valid"]["n"], "v2": v["v2_valid"]["n"]}
                   for k, v in by(lambda r: r["created_at"].astimezone(timezone.utc).date().isoformat()).items()},
        "v1_reasons": dict(Counter(r["v1"].get("reason") or "VALID" for r in out)),
        "v2_reasons": dict(Counter(r["v2"].get("reason") or "VALID" for r in out)),
        "v2_policy_timeouts_accepted": sum(bool(r["v2"].get("policy_timeout_accepted")) for r in out),
        "v2_rows_with_degraded_flow": sum((r["v2"].get("degraded_flow_decisions") or 0) > 0 for r in out),
    }
    return census, proofs_v1, proofs_v2


def _fit(service, records, *, ml_config, feature_columns, seed):
    """Mirror of the L3_PROFILE CatBoost branch of train_challengers, minus persistence."""
    import numpy as np
    from app.ml.indicator_intelligence import inverse_group_frequency_weights
    from app.ml.promotion_gate import evaluate_promotion_gate
    from app.services.ml_challenger_service import (
        _require_positive_int_config, _stable_train_feature_indices, _train_catboost_sync,
    )
    cfg = ml_config
    X, y, cols, cat_idx, returns, created_at, ids, holding, groups = service._build_l3_dataset(
        records, feature_columns, float(cfg["ml_win_fast_threshold_seconds"]), lane_name="L3_PROFILE",
        lane_contract=(cfg.get("ml_feature_contract") or {}).get("L3_PROFILE"), feature_ranges=cfg.get("ml_feature_ranges"),
        backfilled_feature_names=[str(x) for x in (cfg.get("ml_backfilled_feature_names") or []) if x],
        backfill_marker_key=str(cfg.get("ml_backfill_marker_key") or ""), label_objective=str(cfg["ml_label_objective"]),
        fee_roundtrip_pct=float(cfg["ml_fee_roundtrip_pct"]))
    report = {"rows": int(len(y)), "positives": int(y.sum()), "negatives": int(len(y) - y.sum()),
              "positive_rate": round(float(y.mean()), 4) if len(y) else None}
    split = service._chronological_split_with_embargo(
        X, y, metadata=[returns, created_at, ids, groups], created_at=created_at, holding_seconds=holding, group_ids=groups,
        val_fraction=float(cfg["ml_catboost_validation_size_ratio"]), test_fraction=float(cfg["ml_catboost_test_size_ratio"]),
        embargo_seconds=int(cfg.get("ml_split_embargo_seconds", int(cfg["ml_win_fast_threshold_seconds"]) + 3600)),
        min_train_size=int(cfg["ml_catboost_min_train_samples"]), min_validation_size=int(cfg["ml_catboost_min_validation_samples"]),
        min_test_size=int(cfg["ml_catboost_min_test_samples"]),
        max_boundary_candidates=_require_positive_int_config(cfg, "ml_catboost_max_boundary_candidates"))
    report["split"] = {k: split["split_diagnostics"].get(k) for k in (
        "split_strategy", "raw_train", "raw_validation", "raw_test", "train_from", "validation_from", "test_from", "test_to",
        "block_reason")}
    if not split["has_test"]:
        report["status"] = "no_feasible_holdout"
        return report
    y_tr, y_va, y_te = split["y_tr"], split["y_va"], split["y_te"]
    report["partitions"] = {name: {"n": int(len(part)), "negatives": int(len(part) - part.sum()),
                                   "positive_rate": round(float(part.mean()), 4)}
                            for name, part in (("train", y_tr), ("validation", y_va), ("test", y_te))}
    report["independent_test_events"] = len(set(split["meta_te"][3]))
    weights = [inverse_group_frequency_weights(split[k][3]) for k in ("meta_tr", "meta_va", "meta_te")]
    keep = _stable_train_feature_indices(split["X_tr"], cols, min_coverage=float(cfg.get("ml_feature_min_coverage_pct", 0.30)),
                                         excluded=[str(x) for x in (cfg.get("ml_l3_feature_exclusions") or [])])
    if not keep:
        report["status"] = "no_stable_features"
        return report
    names = [cols[i] for i in keep]
    cat = [keep.index(i) for i in cat_idx if i in keep]
    result = _train_catboost_sync(
        split["X_tr"][:, keep], y_tr, split["X_va"][:, keep], y_va, names, int(cfg["ml_optuna_max_trials"]), cat,
        split["X_te"][:, keep], y_te, split["meta_va"][0], split["meta_te"][0],
        float(cfg.get("ml_threshold_grid_step", 0.01)), int(cfg.get("ml_threshold_min_positives", 10)),
        *weights, "net_ev", None, search_space=(cfg.get("ml_optuna_search_space") or {}).get("catboost"),
        base_params=cfg.get("ml_catboost_base_params"), seed=seed,
        optuna_timeout_s=_require_positive_int_config(cfg, "ml_optuna_timeout_seconds"),
        early_stopping_rounds=_require_positive_int_config(cfg, "ml_catboost_early_stopping_rounds"),
        test_times=split["meta_te"][1], test_groups=split["meta_te"][3],
        bootstrap_iterations=_require_positive_int_config(cfg, "ml_approval_bootstrap_iterations"))
    test, val = result["test_metrics"], result["metrics"]
    test_returns = np.asarray(split["meta_te"][0], dtype=float)
    gate = evaluate_promotion_gate({"metrics_json": {"test": test, "validation": val}, "model_lane": "L3_PROFILE",
                                    "test_samples": test.get("samples")}, promotion_config=cfg)
    report.update(status="trained", seed=seed, threshold=result["threshold"], features=len(names),
                  validation={k: val.get(k) for k in ("roc_auc", "precision", "recall", "f1", "positive_rate", "val_samples")},
                  test={k: test.get(k) for k in ("roc_auc", "roc_auc_ci_low", "roc_auc_ci_high", "precision", "recall", "fpr",
                                                 "samples", "selected_samples", "net_ev", "distinct_days", "independent_events")},
                  test_baseline_net_ev_all_rows=round(float(test_returns.mean()), 4),
                  quantitative_gate_reasons=[r for r in gate["reasons"] if not r.startswith("missing_")])
    if test.get("net_ev") is not None:
        report["test_net_ev_uplift_vs_all_rows"] = round(float(test["net_ev"]) - float(test_returns.mean()), 4)
    return report


async def run(args):
    from app.database import CeleryAsyncSessionLocal
    from app.ml.feature_extractor import FEATURE_COLUMNS
    from app.ml.l3_managed_exit import CERTIFICATION_V2, certification_rules, definition, lane_config
    from app.services.ml_challenger_service import LOOKBACK_DAYS, MLChallengerService
    rules_v2 = certification_rules({"version": CERTIFICATION_V2, "flow_evidence": args.flow_evidence,
                                    "timeout_horizon": args.timeout_horizon})
    cutoff = datetime.fromisoformat(args.cutoff) if args.cutoff else datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        raise ValueError("--cutoff needs an explicit timezone")
    service = MLChallengerService()
    async with CeleryAsyncSessionLocal() as db:
        # Session-level so the periodic commits below (which release the MVCC
        # snapshot on a long census) can never open a writable transaction.
        await db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        await db.commit()
        owner = (await db.execute(text("""SELECT user_id::text, config_json FROM config_profiles
            WHERE config_type='ml' AND is_active = true LIMIT 1"""))).one()
        user_id = args.user_id or owner[0]
        raw = owner[1] if isinstance(owner[1], dict) else json.loads(owner[1])
        ml_config = lane_config(raw)
        spec = definition(ml_config)
        if spec is None:
            raise ValueError("ml_l3_managed_exit is not configured; nothing to compare")
        from app.ml.dataset_config import parse_required_ml_dataset_valid_from
        valid_from = parse_required_ml_dataset_valid_from(
            {"ml_dataset_valid_from": ml_config.get("ml_l3_dataset_valid_from") or ml_config["ml_dataset_valid_from"]})
        fee = float(spec["fee_roundtrip_pct"]) + float(spec["slippage_roundtrip_pct"])
        census, proofs_v1, proofs_v2 = await _census(db, user_id=user_id, spec=spec, valid_from=valid_from, cutoff=cutoff,
                                                      rules_v2=rules_v2, fee=fee, limit=args.limit,
                                                      batch_size=args.batch_size)
        lookback = args.lookback_days or LOOKBACK_DAYS
        official, official_meta = await service._prepare_catboost_gate_records(
            db, user_id, lookback_days=lookback, cb_sources=["L3"], dataset_query_cutoff=cutoff, ml_config=ml_config)
        v2_records, v2_meta = await service._prepare_catboost_gate_records(
            db, user_id, lookback_days=lookback, cb_sources=["L3"], dataset_query_cutoff=cutoff, ml_config=ml_config,
            managed_label_overrides=proofs_v2)
        override_stats = dict(getattr(service, "_last_managed_override_stats", {}))
    report = {"read_only": True, "cutoff": cutoff.isoformat(), "user_id": user_id, "contract_hash": spec["hash"],
              "certification_v2_rules": rules_v2, "fee_plus_slippage_pct": fee, "census": census,
              "trainer_population": {"v1_official_rows": len(official), "v2_rows": len(v2_records),
                                     "v2_override_stats": override_stats,
                                     "v1_barrier_contract": official_meta.get("barrier_contract"),
                                     "v2_barrier_contract": v2_meta.get("barrier_contract")}}
    if not args.skip_training:
        seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else [int(ml_config["ml_training_seed"])]
        report["training"] = {}
        for name, records in (("v1_official", official), ("v2_replay", v2_records)):
            report["training"][name] = [await asyncio.to_thread(
                _fit, service, records, ml_config=ml_config, feature_columns=list(FEATURE_COLUMNS), seed=seed)
                for seed in seeds]
            print(f"[train] {name} done", flush=True)
    payload = json.dumps(report, default=str, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload)
    print(payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--flow-evidence", required=True, choices=("REQUIRE_VALID", "REPLAY_FALLBACK"))
    parser.add_argument("--timeout-horizon", required=True, choices=("EXIT_AT", "POLICY_TIMEOUT"))
    parser.add_argument("--cutoff", help="UTC ISO timestamp (default: now)")
    parser.add_argument("--user-id")
    parser.add_argument("--lookback-days", type=int)
    parser.add_argument("--seeds", help="comma-separated seeds (default: ml_training_seed)")
    parser.add_argument("--limit", type=int, help="census only the first N shadows (smoke test)")
    parser.add_argument("--batch-size", type=int, default=50, help="shadows per evidence round trip (1 = per-shadow loader)")
    parser.add_argument("--skip-training", action="store_true")
    parser.add_argument("--output")
    asyncio.run(run(parser.parse_args()))
