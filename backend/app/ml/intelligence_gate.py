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

REQUIRED_INDICATOR_CONFIG_KEYS = (
    "ml_approved_intelligence_min_effective_test_snapshots",
    "ml_approved_intelligence_min_replicated_findings",
    "ml_approved_intelligence_min_distinct_indicators",
    "ml_approved_intelligence_min_prioritize_findings",
    "ml_approved_intelligence_min_block_findings",
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


def evaluate_indicator_intelligence_gate(
    metrics: Dict[str, Any], config: Dict[str, Any]
) -> Dict[str, Any]:
    """Approve replicated descriptive findings, never execution authority.

    This lane answers which entry-time indicator buckets repeat their direction
    in validation and test.  It deliberately does not claim that the aggregate
    CatBoost probability is a deployable trade predictor.
    """
    missing = [key for key in REQUIRED_INDICATOR_CONFIG_KEYS if key not in config]
    if missing:
        return {
            "status": "BLOCKED",
            "reasons": [f"missing_approved_intelligence_config:{','.join(missing)}"],
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "execution_authority": False,
            "basis": "replicated_indicator_findings",
            "thresholds": {},
            "metrics": {},
        }

    report = metrics.get("indicator_intelligence") or {}
    findings = report.get("findings") or []
    actionable = [item for item in findings if item.get("action") in {"PRIORITIZE", "BLOCK_CANDIDATE"}]
    prioritize = [item for item in actionable if item.get("action") == "PRIORITIZE"]
    blocked = [item for item in actionable if item.get("action") == "BLOCK_CANDIDATE"]
    distinct = {str(item.get("indicator")) for item in actionable if item.get("indicator")}
    effective = (metrics.get("test") or {}).get("effective_snapshots")

    minimum_effective = int(config["ml_approved_intelligence_min_effective_test_snapshots"])
    minimum_findings = int(config["ml_approved_intelligence_min_replicated_findings"])
    minimum_indicators = int(config["ml_approved_intelligence_min_distinct_indicators"])
    minimum_prioritize = int(config["ml_approved_intelligence_min_prioritize_findings"])
    minimum_block = int(config["ml_approved_intelligence_min_block_findings"])
    reasons: list[str] = []
    if effective is None or float(effective) < minimum_effective:
        reasons.append("effective_test_snapshots_below_minimum")
    if len(actionable) < minimum_findings:
        reasons.append("replicated_findings_below_minimum")
    if len(distinct) < minimum_indicators:
        reasons.append("distinct_indicators_below_minimum")
    if len(prioritize) < minimum_prioritize:
        reasons.append("prioritize_findings_below_minimum")
    if len(blocked) < minimum_block:
        reasons.append("block_findings_below_minimum")
    return {
        "status": "REJECTED" if reasons else "APPROVED",
        "reasons": reasons,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "execution_authority": False,
        "basis": "replicated_indicator_findings",
        "thresholds": {
            "min_effective_test_snapshots": minimum_effective,
            "min_replicated_findings": minimum_findings,
            "min_distinct_indicators": minimum_indicators,
            "min_prioritize_findings": minimum_prioritize,
            "min_block_findings": minimum_block,
        },
        "metrics": {
            "effective_test_snapshots": effective,
            "replicated_findings": len(actionable),
            "distinct_indicators": len(distinct),
            "prioritize_findings": len(prioritize),
            "block_findings": len(blocked),
        },
    }
