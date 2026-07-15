"""Governance rules shared by Profile Intelligence and ML registries."""
from __future__ import annotations

from typing import Any, Iterable


FORWARD_STAGES = (
    "discovery",
    "temporal_validation",
    "shadow_forward",
    "human_approval",
    "limited_live",
    "full_live",
)

MAX_AUTONOMY_LEVEL = 3


def source_profile_attribution(
    trades: Iterable[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Return stable profile names and ids from matched evidence rows."""
    pairs: dict[str, str] = {}
    for trade in trades:
        profile_id = trade.get("profile_id")
        profile_name = trade.get("profile_name")
        if profile_id:
            pairs[str(profile_id)] = str(profile_name or profile_id)
    ordered = sorted(pairs.items(), key=lambda item: (item[1].lower(), item[0]))
    return [name for _, name in ordered], [profile_id for profile_id, _ in ordered]


def suggestion_registry_block_reasons(suggestion: Any) -> list[str]:
    """Enforce the minimum traceability contract for an applicable suggestion."""
    reasons: list[str] = []
    required = {
        "source_type": getattr(suggestion, "source_type", None),
        "source_run_id": getattr(suggestion, "source_run_id", None),
        "profile_id": getattr(suggestion, "profile_id", None),
        "diff_json": getattr(suggestion, "diff_json", None),
        "rollback_payload": getattr(suggestion, "rollback_payload", None),
    }
    for field, value in required.items():
        if value is None:
            reasons.append(f"missing_{field}")
    if getattr(suggestion, "validation_status", None) != "validated":
        reasons.append("validation_not_validated")
    if getattr(suggestion, "actionability_status", None) in {
        None,
        "exploratory_only",
        "not_actionable",
    }:
        reasons.append("suggestion_not_actionable")
    dataset_version = str(getattr(suggestion, "dataset_version", None) or "")
    if not dataset_version.startswith("pi-native-point-in-time-v1:"):
        reasons.append("dataset_not_official_native")
    if getattr(suggestion, "feature_schema_version", None) != "entry_features_v2":
        reasons.append("feature_schema_not_official")
    if getattr(suggestion, "label_version", None) != "shadow_outcome-v1":
        reasons.append("label_contract_not_official")
    return reasons


def forward_transition_block_reason(
    current_stage: str,
    target_stage: str,
    *,
    validation_status: str | None,
    shadow_forward_passed: bool,
    human_approved: bool,
    rollback_available: bool,
) -> str | None:
    if current_stage not in FORWARD_STAGES or target_stage not in FORWARD_STAGES:
        return "invalid_forward_stage"
    if FORWARD_STAGES.index(target_stage) != FORWARD_STAGES.index(current_stage) + 1:
        return "non_sequential_forward_transition"
    if target_stage in {"shadow_forward", "human_approval", "limited_live", "full_live"}:
        if validation_status != "validated":
            return "blocked_no_out_of_sample_validation"
    if target_stage in {"human_approval", "limited_live", "full_live"}:
        if not shadow_forward_passed:
            return "blocked_shadow_forward_incomplete"
    if target_stage in {"limited_live", "full_live"}:
        if not human_approved:
            return "blocked_human_approval_required"
        if not rollback_available:
            return "blocked_rollback_required"
    return None


def autonomy_block_reason(
    requested_level: int,
    *,
    configured_maximum_level: int = 2,
    forward_validation: bool = False,
    auto_rollback: bool = False,
    impact_limit: bool = False,
    cooldown: bool = False,
    max_changes_per_day: bool = False,
    risk_budget: bool = False,
    post_change_monitoring: bool = False,
) -> str | None:
    if requested_level < 0 or requested_level > 5:
        return "invalid_autonomy_level"
    if configured_maximum_level > MAX_AUTONOMY_LEVEL:
        return "configured_autonomy_exceeds_safe_cap"
    if requested_level > configured_maximum_level:
        return "requested_autonomy_exceeds_policy"
    if requested_level >= 3 and not (
        forward_validation
        and impact_limit
        and cooldown
        and max_changes_per_day
        and risk_budget
        and post_change_monitoring
    ):
        return "limited_live_controls_incomplete"
    if requested_level >= 4:
        if not auto_rollback:
            return "auto_rollback_required"
        return "autonomy_level_4_5_disabled"
    return None


def production_model_block_reason(
    *,
    registry_status: str,
    model_type: str,
    operational: bool,
) -> str | None:
    if model_type in {"lightgbm", "catboost"} and not operational:
        return f"{model_type}_not_operational"
    if registry_status != "champion":
        return "challenger_cannot_control_production"
    return None
