"""Promotion Gate — objective eligibility check for ml_models before they can be
trusted by ranking/inference (status='active' is no longer sufficient on its own).

Part of the Profile Intelligence Adaptive Loop reformulation (audit 2026-06-24).
Root cause addressed: v44 (CatBoost/L3_PROFILE) and v46 (LightGBM/L1_SPECTRUM)
were marked status='active' with test ROC AUC < 0.5 (anti-predictive on holdout),
because no gate existed to block that promotion.

This module is pure evaluation logic (no DB I/O) so it can be unit-tested in
isolation. DB persistence of the result lives in:
  - MLChallengerService._save_to_db()              — auto-evaluate new candidates
  - backend/scripts/backfill_model_promotion_gate.py — re-evaluate existing models
  - POST /api/ml/models/{id}/evaluate-promotion-gate — manual re-evaluation
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Thresholds — tunable, but never bypassable for the absolute rule (#13 below).
REQUIRED_CONFIG_KEYS = (
    "ml_promotion_min_test_auc",
    "ml_promotion_min_test_samples",
    "ml_promotion_max_val_test_gap",
    "ml_promotion_max_test_fpr",
    "ml_promotion_require_positive_net_ev",
)

APPROVED = "APPROVED"
REJECTED = "REJECTED"
BLOCKED = "BLOCKED"


def evaluate_promotion_gate(
    model_row: Dict[str, Any],
    *,
    promotion_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate whether a ml_models row is eligible to be used in ranking/inference.

    Args:
        model_row: dict with at least the keys produced by a `SELECT *` (or the
            equivalent subset) of `ml_models` — specifically: `metrics_json`,
            `roc_auc`, `test_samples`, `feature_count`, `label_version`,
            `model_lane`, `source_filter`, `dataset_contract_id`.

    Returns:
        {
          "status": "APPROVED" | "REJECTED" | "BLOCKED",
          "evaluated_at": iso8601 str,
          "reasons": [str, ...],     # why REJECTED/BLOCKED (empty if APPROVED)
          "thresholds": {...},       # thresholds used for this evaluation
          "metrics": {...},          # the metrics actually evaluated
        }

    Semantics:
        REJECTED — the model's own numbers fail a quantitative bar (bad model).
        BLOCKED  — required lineage/metadata is missing (can't be trusted/audited
                   yet, regardless of how good the numbers look).
        REJECTED takes precedence over BLOCKED if both apply.
    """
    cfg = promotion_config or {}
    missing_cfg = [key for key in REQUIRED_CONFIG_KEYS if key not in cfg]
    if missing_cfg:
        return {
            "status": BLOCKED,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "reasons": [f"missing_promotion_config:{','.join(missing_cfg)}"],
            "thresholds": {},
            "metrics": {},
        }

    min_test_auc = float(cfg["ml_promotion_min_test_auc"])
    min_test_samples = int(cfg["ml_promotion_min_test_samples"])
    max_generalization_gap = float(cfg["ml_promotion_max_val_test_gap"])
    max_test_fpr = float(cfg["ml_promotion_max_test_fpr"])
    require_positive_net_ev = bool(cfg["ml_promotion_require_positive_net_ev"])

    metrics_json = model_row.get("metrics_json") or {}
    test = metrics_json.get("test") or {}
    validation = metrics_json.get("validation") or {}

    test_auc = test.get("roc_auc")
    test_samples = test.get("samples") if test.get("samples") is not None else model_row.get("test_samples")
    test_fpr = test.get("fpr")
    val_auc = validation.get("roc_auc") if validation.get("roc_auc") is not None else model_row.get("roc_auc")

    reasons: list[str] = []
    rejected = False
    blocked = False

    # ---- Rule #13: test AUC below configured minimum -------------------------
    if test_auc is None:
        rejected = True
        reasons.append("missing_test_roc_auc")
    elif test_auc < min_test_auc:
        rejected = True
        reasons.append(f"test_roc_auc_below_min_threshold:{test_auc:.4f}<{min_test_auc}")

    # ---- Rule #3: minimum test sample size -------------------------------------
    if test_samples is None:
        rejected = True
        reasons.append("missing_test_samples")
    elif test_samples < min_test_samples:
        rejected = True
        reasons.append(f"test_samples_below_minimum:{test_samples}<{min_test_samples}")

    # ---- Rule #4: generalization gap (overfitting signature) ------------------
    if test_auc is not None and val_auc is not None:
        gap = abs(val_auc - test_auc)
        if gap > max_generalization_gap:
            rejected = True
            reasons.append(
                f"generalization_gap_exceeded:{gap:.4f}>{max_generalization_gap}"
            )
    else:
        reasons.append("generalization_gap_not_evaluated_missing_auc")

    # ---- Rule #5: test false positive rate ceiling -----------------------------
    if test_fpr is not None and test_fpr > max_test_fpr:
        rejected = True
        reasons.append(f"test_fpr_exceeded:{test_fpr:.4f}>{max_test_fpr}")

    test_net_ev = test.get("net_ev") if isinstance(test, dict) else None
    if test_net_ev is None:
        test_net_ev = metrics_json.get("test_net_ev")
    if require_positive_net_ev:
        if test_net_ev is None:
            rejected = True
            reasons.append("missing_test_net_ev")
        elif float(test_net_ev) <= 0.0:
            rejected = True
            reasons.append(f"test_net_ev_not_positive:{float(test_net_ev):.6f}")

    # ---- Rule #6-9: required lineage metadata (BLOCKED, not REJECTED) ---------
    feature_count = model_row.get("feature_count")
    if not feature_count or feature_count <= 0:
        blocked = True
        reasons.append("missing_feature_count")

    label_version = model_row.get("label_version") or metrics_json.get("label_version")
    if not label_version:
        blocked = True
        reasons.append("missing_label_version")

    model_lane = model_row.get("model_lane")
    if not model_lane:
        blocked = True
        reasons.append("missing_model_lane")

    source_filter = model_row.get("source_filter") or model_row.get("training_scope")
    if not source_filter:
        blocked = True
        reasons.append("missing_train_sources")

    dataset_contract_id = model_row.get("dataset_contract_id")
    if not dataset_contract_id:
        blocked = True
        reasons.append("missing_dataset_policy")

    for key in ("train_from", "train_to", "dataset_query_cutoff", "dataset_hash"):
        if not model_row.get(key):
            blocked = True
            reasons.append(f"missing_{key}")

    # ---- Rule #11/#12: leakage flag / positive rate sanity (best-effort) -------
    leakage_detected = metrics_json.get("leakage_detected")
    if leakage_detected:
        rejected = True
        reasons.append("leakage_detected")

    positive_rate = metrics_json.get("positive_rate")
    if positive_rate is not None and (positive_rate <= 0.0 or positive_rate >= 1.0):
        rejected = True
        reasons.append(f"degenerate_positive_rate:{positive_rate}")

    if rejected:
        status = REJECTED
    elif blocked:
        status = BLOCKED
    else:
        status = APPROVED

    return {
        "status": status,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "reasons": reasons,
        "thresholds": {
            "min_test_auc": min_test_auc,
            "min_test_samples": min_test_samples,
            "max_generalization_gap": max_generalization_gap,
            "max_test_fpr": max_test_fpr,
            "require_positive_net_ev": require_positive_net_ev,
        },
        "metrics": {
            "test_roc_auc": test_auc,
            "val_roc_auc": val_auc,
            "test_samples": test_samples,
            "test_fpr": test_fpr,
            "test_net_ev": test_net_ev,
        },
    }


def is_eligible(model_row: Dict[str, Any], **kwargs) -> bool:
    """Convenience wrapper — True only if status == APPROVED."""
    return evaluate_promotion_gate(model_row, **kwargs)["status"] == APPROVED


def merge_promotion_gate_into_metrics_json(
    metrics_json: Optional[Dict[str, Any]], gate_result: Dict[str, Any]
) -> Dict[str, Any]:
    """Returns a new metrics_json dict with `promotion_gate` set, preserving
    every other existing key. Never mutates the input in place."""
    merged = dict(metrics_json or {})
    merged["promotion_gate"] = gate_result
    return merged
