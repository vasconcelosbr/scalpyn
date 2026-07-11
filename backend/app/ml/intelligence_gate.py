"""Governance for advisory-only ML intelligence models.

This gate deliberately does not grant execution authority.  It evaluates whether
probability estimates are sufficiently discriminative, calibrated and stable to
support explanations such as PRIORITIZE/BLOCK_CANDIDATE.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


REQUIRED_CONFIG_KEYS = (
    "ml_intelligence_min_test_auc",
    "ml_intelligence_min_effective_test_snapshots",
    "ml_intelligence_max_val_test_gap",
    "ml_intelligence_max_test_brier",
)


def evaluate_intelligence_gate(metrics: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    missing = [key for key in REQUIRED_CONFIG_KEYS if key not in config]
    if missing:
        return {
            "status": "BLOCKED",
            "reasons": [f"missing_intelligence_config:{','.join(missing)}"],
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "execution_authority": False,
            "thresholds": {},
            "metrics": {},
        }

    validation = metrics.get("validation") or {}
    test = metrics.get("test") or {}
    min_auc = float(config["ml_intelligence_min_test_auc"])
    min_effective = int(config["ml_intelligence_min_effective_test_snapshots"])
    max_gap = float(config["ml_intelligence_max_val_test_gap"])
    max_brier = float(config["ml_intelligence_max_test_brier"])
    test_auc = test.get("weighted_roc_auc")
    val_auc = validation.get("weighted_roc_auc")
    effective = test.get("effective_snapshots")
    brier = test.get("weighted_brier")
    reasons: list[str] = []
    if test_auc is None or float(test_auc) < min_auc:
        reasons.append("test_auc_below_intelligence_minimum")
    if effective is None or float(effective) < min_effective:
        reasons.append("effective_test_snapshots_below_minimum")
    if test_auc is None or val_auc is None or abs(float(val_auc) - float(test_auc)) > max_gap:
        reasons.append("intelligence_generalization_gap_exceeded")
    if brier is None or float(brier) > max_brier:
        reasons.append("test_brier_exceeded")
    return {
        "status": "REJECTED" if reasons else "APPROVED",
        "reasons": reasons,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "execution_authority": False,
        "thresholds": {
            "min_test_auc": min_auc,
            "min_effective_test_snapshots": min_effective,
            "max_val_test_gap": max_gap,
            "max_test_brier": max_brier,
        },
        "metrics": {
            "validation_auc": val_auc,
            "test_auc": test_auc,
            "effective_test_snapshots": effective,
            "test_brier": brier,
        },
    }
