"""Fail-closed ML governance shared by producers and consumers."""

from __future__ import annotations

from typing import Any, Mapping


DESCRIPTIVE_VALIDATED = "DESCRIPTIVE_VALIDATED"
PREDICTIVE_REJECTED = "PREDICTIVE_REJECTED"
PREDICTIVE_APPROVED = "PREDICTIVE_APPROVED_FOR_INTELLIGENCE"


def governance_from_gate(
    *,
    descriptive_gate: Mapping[str, Any] | None,
    predictive_gate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build explicit authorities; no authority is inferred from findings."""
    descriptive_ok = (descriptive_gate or {}).get("status") == "APPROVED"
    predictive_ok = (predictive_gate or {}).get("status") == "APPROVED"
    return {
        "descriptive_status": (
            DESCRIPTIVE_VALIDATED if descriptive_ok else "DESCRIPTIVE_REJECTED"
        ),
        "predictive_status": PREDICTIVE_APPROVED if predictive_ok else PREDICTIVE_REJECTED,
        "calibration_authority": predictive_ok,
        "rule_generation_authority": predictive_ok,
        "autopilot_authority": False,
        "execution_authority": False,
    }


def can_publish_ml_evidence(model: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Authorize evidence publication only for explicitly approved predictors."""
    reasons: list[str] = []
    if model.get("predictive_status") != PREDICTIVE_APPROVED:
        reasons.append("predictive_model_not_approved_for_intelligence")
    if model.get("calibration_authority") is not True:
        reasons.append("calibration_authority_denied")
    if model.get("rule_generation_authority") is not True:
        reasons.append("rule_generation_authority_denied")
    return not reasons, reasons


def can_execute(model: Mapping[str, Any]) -> bool:
    return model.get("execution_authority") is True
