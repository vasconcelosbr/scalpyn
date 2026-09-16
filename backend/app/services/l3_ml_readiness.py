"""Read-only L3 certification using the exact trainer population and split."""
from datetime import datetime, timezone
import hashlib
import json
from sqlalchemy import text


async def l3_readiness(db, user_id, *, cutoff=None, config=None, lookback_days=None):
    from .ml_challenger_service import MLChallengerService, LOOKBACK_DAYS
    from app.ml.feature_extractor import FEATURE_COLUMNS
    from app.ml.l3_integrity import contract_definitions
    cutoff = cutoff or datetime.now(timezone.utc)
    if config is None:
        row = (await db.execute(text("""SELECT config_json FROM config_profiles
            WHERE user_id=:uid AND config_type='ml' AND is_active=TRUE LIMIT 1"""), {"uid": str(user_id)})).fetchone()
        if not row:
            raise ValueError("ml_config_missing")
        config = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    service = MLChallengerService()
    pi_row = (await db.execute(text("""SELECT config_json FROM config_profiles
        WHERE user_id=:uid AND config_type='profile_intelligence' AND is_active=TRUE LIMIT 1"""), {"uid": str(user_id)})).fetchone()
    pi_config = (json.loads(pi_row[0]) if isinstance(pi_row[0], str) else pi_row[0]) if pi_row else {}
    records, meta = await service._prepare_catboost_gate_records(
        db, user_id, lookback_days=lookback_days or LOOKBACK_DAYS, cb_sources=["L3"],
        dataset_query_cutoff=cutoff, ml_config=config, collect_diagnostics=True,
    )
    minimum = int(config["ml_catboost_retrain_min_eligible_rows"])
    ids = sorted(str(r["shadow_id"]) for r in records)
    result = {"model_lane": "L3_PROFILE", "dataset_policy": "L3_ONLY", "source": "L3",
              "label_version": config["ml_label_version"], "dataset_query_cutoff": cutoff.isoformat(),
              "contract": contract_definitions(config, FEATURE_COLUMNS),
              "population_hash": hashlib.sha256("|".join(ids).encode()).hexdigest(),
              "total_rows": len(records), "labelable_rows": 0, "min_required": minimum,
              "candidate_ready": False, "promotion_ready": False, "ready": False,
              "blocked_reasons": [], "funnel": meta, "split": None,
              "training_started": False, "execution_authority": False}
    result["automatic_training_enabled"] = bool((pi_config or {}).get("enable_catboost", False))
    if not records:
        result["blocked_reasons"] = ["insufficient_contract_valid_rows"]
        return result
    built = service._build_l3_dataset(records, FEATURE_COLUMNS, config["ml_win_fast_threshold_seconds"],
        lane_name="L3_PROFILE", lane_contract=(config.get("ml_feature_contract") or {}).get("L3_PROFILE"),
        feature_ranges=config.get("ml_feature_ranges"), backfilled_feature_names=config.get("ml_backfilled_feature_names"),
        backfill_marker_key=config.get("ml_backfill_marker_key"), label_objective=config["ml_label_objective"],
        fee_roundtrip_pct=float(config["ml_fee_roundtrip_pct"]))
    result["labelable_rows"] = len(built[1])
    if len(built[1]) < minimum:
        result["blocked_reasons"] = ["insufficient_labelable_rows"]
        return result
    split = service._chronological_split_with_embargo(built[0], built[1],
        metadata=[built[4], built[5], built[6], built[8]], created_at=built[5], holding_seconds=built[7], group_ids=built[8],
        val_fraction=float(config["ml_catboost_validation_size_ratio"]), test_fraction=float(config["ml_catboost_test_size_ratio"]),
        embargo_seconds=int(config["ml_split_embargo_seconds"]), min_train_size=int(config["ml_catboost_min_train_samples"]),
        min_validation_size=int(config["ml_catboost_min_validation_samples"]), min_test_size=int(config["ml_catboost_min_test_samples"]),
        max_boundary_candidates=int(config["ml_catboost_max_boundary_candidates"]))
    result["split"] = split["split_diagnostics"]
    result["candidate_ready"] = bool(split["has_test"])
    if not split["has_test"]:
        result["blocked_reasons"] = ["insufficient_effective_partitions"]
        return result
    test_times, groups = split["meta_te"][1], split["meta_te"][3]
    days = len({t.astimezone(timezone.utc).date() for t in test_times})
    events = len(set(groups))
    result.update(test_distinct_days=days, independent_test_events=events)
    if events < int(config["ml_promotion_min_test_samples"]):
        result["blocked_reasons"].append("insufficient_independent_holdout")
    if days < int(config["ml_approval_min_distinct_days"]):
        result["blocked_reasons"].append("insufficient_holdout_days")
    result["promotion_ready"] = not result["blocked_reasons"]
    result["ready"] = result["promotion_ready"]
    return result
