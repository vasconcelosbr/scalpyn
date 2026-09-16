"""L3_PROFILE train/serve and independent-observation contracts.

Pure functions: no profile mutation, promotion or execution authority.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math

VERSION = "l3_ml_integrity_v1"


def project_capture(snapshot, source_snapshot, config):
    """Keep ML inputs and their raw dependencies; retain context at the decision.

    Optional decision scores without causal evidence become missing, never zero.
    Required fields and future timestamps continue to fail the capture contract.
    """
    from .feature_extractor import FEATURE_COLUMNS, FEATURE_ALIASES, ML_EXCLUDED_FIELDS
    from .feature_contract_v2 import _feature_source_timestamps
    dependencies = {"close", "price", "ema9", "ema21", "ema50", "ema200", "di_plus", "di_minus", "vwap_candle_count", "atr_percent"}
    allowed = set(FEATURE_COLUMNS) | set(FEATURE_ALIASES) | dependencies
    projected = {k: v for k, v in snapshot.items() if k in allowed and k not in ML_EXCLUDED_FIELDS}
    required = set(((config.get("ml_feature_contract") or {}).get("L3_PROFILE") or {}).get("required", []))
    _, errors = _feature_source_timestamps(projected, source_snapshot=source_snapshot)
    missing = {e.split(":", 1)[1] for e in errors if e.startswith("missing_source_timestamp:")}
    optional_scores = {"liquidity_score", "market_structure_score", "momentum_score", "signal_score"}
    neutralized = sorted((missing & optional_scores) - required)
    for name in neutralized:
        projected[name] = None
    return projected, {"version": VERSION, "excluded_observational_fields": sorted(set(snapshot) - set(projected)),
                       "optional_without_provenance": neutralized}


def stable_profile_bucket(profile_id):
    return int(hashlib.md5(str(profile_id).encode()).hexdigest()[:8], 16) % 9999 if profile_id else 9999


def market_event_key(record):
    """Profiles sharing a market event remain in one statistical group."""
    values = [record.get(k) for k in ("symbol", "exchange", "timeframe", "entry_timestamp")]
    if not all(v is not None and str(v) for v in values):
        raise ValueError("l3_market_event_identity_missing")
    stamp = values[-1]
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("l3_market_event_timezone_missing")
    values[-1] = stamp.astimezone(timezone.utc).isoformat()
    values[1] = "gate.io" if values[1] == "gate" else values[1]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def group_weights(groups):
    import numpy as np
    counts = Counter(groups)
    return np.asarray([1.0 / counts[g] for g in groups], dtype=float)


def build_inference_frame(model, features, profile_id=None):
    """Name-based projection; never truncate, pad, or infer a missing schema."""
    import numpy as np
    import pandas as pd
    from .feature_extractor import FEATURE_COLUMNS
    names = getattr(model, "_inference_feature_names", None)
    if names is None:
        names = getattr(model, "feature_names_in_", None)
    if names is None:
        raise ValueError("l3_artifact_feature_names_missing")
    names = list(names)
    known = set(FEATURE_COLUMNS) | {"source_encoded", "profile_id_encoded"}
    if not names or len(set(names)) != len(names) or set(names) - known:
        raise ValueError("l3_artifact_feature_names_invalid")
    expected = getattr(model, "_n_inference_features", None) or getattr(model, "n_features_in_", None)
    if expected and int(expected) != len(names):
        raise ValueError("l3_artifact_feature_count_mismatch")
    required = getattr(model, "_required_feature_names", [])
    values = {**features, "source_encoded": 1, "profile_id_encoded": stable_profile_bucket(profile_id)}
    for name in required:
        if name not in names or not math.isfinite(float(values.get(name, float("nan")))):
            raise ValueError(f"l3_required_feature_unavailable:{name}")
    row = [values.get(name, float("nan")) for name in names]
    cats = list(model.get_cat_feature_indices()) if hasattr(model, "get_cat_feature_indices") else []
    if cats:
        frame = pd.DataFrame([row], columns=names)
        for index in cats:
            frame[names[index]] = frame[names[index]].map(lambda x: str(int(x)))
        return frame
    return np.asarray([row], dtype="float32")


def holdout_statistics(labels, probabilities, returns, threshold, times, groups, *, iterations, seed):
    """Day-cluster CI with event weights; raw rows remain separately visible."""
    import numpy as np
    from sklearn.metrics import roc_auc_score
    if not (len(labels) == len(probabilities) == len(times) == len(groups)):
        raise ValueError("l3_holdout_metadata_length_mismatch")
    if iterations <= 0:
        raise ValueError("invalid_ml_approval_bootstrap_iterations")
    days = []
    for stamp in times:
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if stamp is None or stamp.tzinfo is None:
            raise ValueError("l3_holdout_timestamp_missing")
        days.append(stamp.astimezone(timezone.utc).date().isoformat())
    y, p = np.asarray(labels), np.asarray(probabilities)
    weights = group_weights(groups)
    unique_days = sorted(set(days))
    blocks = [np.flatnonzero(np.asarray(days) == d) for d in unique_days]
    rng = np.random.default_rng(seed)
    aucs = []
    if len(blocks) >= 2:
        for _ in range(iterations):
            idx = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
            if len(np.unique(y[idx])) == 2:
                aucs.append(float(roc_auc_score(y[idx], p[idx], sample_weight=weights[idx])))
    selected = p >= threshold
    result = {
        "distinct_days": len(unique_days),
        "independent_events": len(set(groups)),
        "selected_events": len({g for g, keep in zip(groups, selected) if keep}),
        "selected_samples": int(selected.sum()),
        "effective_snapshots": len(set(groups)),
        "roc_auc_ci_low": float(np.quantile(aucs, .025)) if aucs else None,
        "roc_auc_ci_high": float(np.quantile(aucs, .975)) if aucs else None,
        "uncertainty_method": "utc_day_cluster_bootstrap_event_weighted_v1",
        "bootstrap_iterations": iterations,
        "bootstrap_valid_iterations": len(aucs),
        "bootstrap_seed": seed,
        "uncertainty_unavailable_reason": None if aucs else "insufficient_temporal_class_diversity",
    }
    if returns is not None and selected.any():
        result["net_ev"] = float(np.average(np.asarray(returns)[selected], weights=weights[selected]))
    return result


def contract_definitions(config, feature_names):
    """Immutable full definitions stored in the existing contract registry."""
    from .l3_managed_exit import lane_config, definition, OUTCOMES
    config = lane_config(config)
    managed = definition(config)
    from .feature_contract_v2 import CAPTURE_CONTRACT_VERSION
    from .feature_extractor import FEATURE_SCHEMA_VERSION
    def identity(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
    required = ["ml_label_version", "ml_label_objective", "ml_active_barrier_contract_version", "ml_fee_roundtrip_pct", "ml_win_fast_threshold_seconds"]
    missing = [k for k in required if config.get(k) is None]
    if missing:
        raise ValueError("l3_contract_config_missing:" + ",".join(missing))
    label = {"version": VERSION, "label_version": config["ml_label_version"], "objective": config["ml_label_objective"],
             "formula": "net_return_pct > 0" if config["ml_label_objective"] == "positive_net_return" else "TP_HIT within target_window_seconds",
             "economic_config": {k: v for k, v in config.items() if k.startswith(("shadow_", "ml_fee_", "ml_label_", "ml_l3_label_", "ml_maturity_")) or k in ("ml_active_barrier_contract_version", "ml_win_fast_threshold_seconds")},
             "outcomes": ["TP_HIT", "SL_HIT", "TIMEOUT"], "net_return_required": True}
    if managed:
        label.update(managed_exit=managed, outcomes=list(OUTCOMES), formula="gross_return_pct - fee_roundtrip_pct - slippage_roundtrip_pct > 0", maturity="max(entry+horizon,label_available_at)+embargo", censored="exclude; do not force exit", evidence="contiguous closed candles; replay; warmup price-only then VALID flow")
    features = {"version": VERSION, "schema": FEATURE_SCHEMA_VERSION, "capture": CAPTURE_CONTRACT_VERSION,
                "ordered_features": list(feature_names), "row_contract": (config.get("ml_feature_contract") or {}).get("L3_PROFILE"),
                "ranges": config.get("ml_feature_ranges"), "exclusions": config.get("ml_l3_feature_exclusions"),
                "transform_version": VERSION}
    dataset = {"version": VERSION, "lane": "L3_PROFILE", "source": "L3", "label_id": identity(label), "feature_id": identity(features),
               "barrier_contract": config["ml_active_barrier_contract_version"], "measurement": "latest_at_cutoff:READY/OK",
               "event_group": "symbol/exchange/timeframe/entry_timestamp", "frontier": config.get("ml_l3_dataset_valid_from") or config.get("ml_dataset_valid_from"),
               "historical_policy": {k: v for k, v in config.items() if k.startswith("ml_l3_historical_")}}
    return {"label": label, "features": features, "dataset": dataset,
            "label_id": identity(label), "feature_id": identity(features), "dataset_id": identity(dataset)}
