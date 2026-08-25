"""Observational L3 gate compiler with independent signal and entry sections.

The v2 contract is deliberately shadow-only.  It computes the decision that
would be produced when both configured sections pass, but it has no execution
consumer.  Promotion requires a separate governed change after a natural
canary and explicit human approval.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable

from .indicator_validity import RuleStatus
from .rule_engine import RuleEngine


CONTRACT_VERSION = "l3_gate_v2"
CANONICALIZATION_VERSION = "canonical_json_v1"
PROMOTION_STATUS = "SHADOW_ONLY"


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _condition_id(condition: Dict[str, Any], index: int) -> str:
    return str(
        condition.get("id")
        or condition.get("field")
        or condition.get("indicator")
        or condition.get("left")
        or f"condition_{index + 1}"
    )


def _canonical_indicator(value: Any) -> Any:
    # The UI calls the robust alpha score simply ``score``.  Runtime scoring
    # exposes it as alpha_score; keeping the alias here prevents a silent
    # missing-field SKIP.
    return "alpha_score" if value == "score" else value


def compile_conditions(
    conditions: Iterable[Dict[str, Any]], *, section: str
) -> list[Dict[str, Any]]:
    """Losslessly adapt UI-authored conditions to RuleEngine's contract."""

    compiled: list[Dict[str, Any]] = []
    for index, raw in enumerate(conditions or []):
        condition = deepcopy(raw)
        condition["id"] = _condition_id(condition, index)
        condition["enabled"] = condition.get("enabled", True)
        condition["required"] = condition.get("required", False)

        if condition.get("type") == "comparison":
            condition["left"] = _canonical_indicator(condition.get("left"))
            condition["right"] = _canonical_indicator(condition.get("right"))
        else:
            source_key = "field" if section == "signals" else "indicator"
            indicator = condition.get(source_key) or condition.get("indicator") or condition.get("field")
            condition["indicator"] = _canonical_indicator(indicator)

        compiled.append(condition)
    return compiled


def _evaluate_section(
    *, section: str, config: Dict[str, Any], eval_data: Dict[str, Any]
) -> Dict[str, Any]:
    logic = str((config or {}).get("logic") or "AND").upper()
    compiled = compile_conditions((config or {}).get("conditions") or [], section=section)
    enabled = [condition for condition in compiled if condition.get("enabled", True)]
    engine = RuleEngine()
    results: list[Dict[str, Any]] = []

    for condition in enabled:
        status, detail = engine.evaluate_condition_status(
            condition, eval_data, field_key="indicator"
        )
        results.append(
            {
                "condition_id": condition["id"],
                "type": condition.get("type", "threshold"),
                "indicator": condition.get("indicator"),
                "left": condition.get("left"),
                "right": condition.get("right"),
                "operator": condition.get("operator", "=="),
                "value": _jsonable(condition.get("value")),
                "min": _jsonable(condition.get("min")),
                "max": _jsonable(condition.get("max")),
                "required": bool(condition.get("required", False)),
                "enabled": True,
                "timeframe": condition.get("timeframe"),
                "period": condition.get("period"),
                "status": status.value,
                "actual": _jsonable(detail.get("actual")),
                "target": _jsonable(detail.get("target")),
                "reason_code": detail.get("reason"),
            }
        )

    configured = bool(enabled)
    required_failures = [
        result["condition_id"]
        for result in results
        if result["required"] and result["status"] == RuleStatus.FAIL.value
    ]
    required_skipped = [
        result["condition_id"]
        for result in results
        if result["required"] and result["status"] == RuleStatus.SKIPPED.value
    ]
    decided_optional = [
        result
        for result in results
        if not result["required"] and result["status"] != RuleStatus.SKIPPED.value
    ]
    optional_passes = [
        result for result in decided_optional if result["status"] == RuleStatus.PASS.value
    ]

    if not configured:
        passed = False
        section_reasons = ["SECTION_NOT_CONFIGURED"]
    elif required_failures:
        passed = False
        section_reasons = ["REQUIRED_CONDITION_FAILED"]
    elif required_skipped:
        passed = False
        section_reasons = ["REQUIRED_CONDITION_SKIPPED"]
    elif not decided_optional:
        # Optional missing data remains observable without becoming a failure.
        # Required missing data is handled fail-closed above.
        passed = True
        section_reasons = ["ALL_DECIDABLE_CONDITIONS_PASSED_OR_SKIPPED"]
    elif logic == "OR":
        passed = bool(optional_passes)
        section_reasons = [] if passed else ["OR_GROUP_NOT_SATISFIED"]
    else:
        passed = len(optional_passes) == len(decided_optional)
        section_reasons = [] if passed else ["AND_GROUP_NOT_SATISFIED"]

    return {
        "section": section,
        "logic": logic,
        "configured": configured,
        "gate_passed": passed,
        "reason_codes": section_reasons,
        "matched": [r["condition_id"] for r in results if r["status"] == RuleStatus.PASS.value],
        "failed": [r["condition_id"] for r in results if r["status"] == RuleStatus.FAIL.value],
        "failed_required": required_failures,
        "skipped": [r["condition_id"] for r in results if r["status"] == RuleStatus.SKIPPED.value],
        "skipped_required": required_skipped,
        "conditions": results,
    }


def evaluate_l3_gate_v2(
    *,
    asset: Dict[str, Any],
    profile_config: Dict[str, Any] | None,
    score: float,
    score_context: Dict[str, Any] | None,
    evaluated_at: datetime,
    base_eligible: bool,
    legacy_decision: str,
    block_rules_audit: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build one immutable point-in-time envelope and evaluate both gates."""

    profile = profile_config or {}
    eval_data = {**asset, **(asset.get("indicators") or {}), "alpha_score": score}
    signals = _evaluate_section(
        section="signals", config=profile.get("signals") or {}, eval_data=eval_data
    )
    entry = _evaluate_section(
        section="entry_triggers",
        config=profile.get("entry_triggers") or {},
        eval_data=eval_data,
    )
    raw_block_audit = deepcopy(block_rules_audit or {})
    block_lineage = deepcopy(profile.get("_block_rules_lineage") or {})
    block_rules = {
        "configured": bool((profile.get("block_rules") or {}).get("blocks")),
        "evaluated": raw_block_audit.get("rules") or [],
        "matched": raw_block_audit.get("matched_blocks") or [],
        "blocked": bool(raw_block_audit.get("blocked")),
        "blocked_by": raw_block_audit.get("blocked_by") or [],
        "skipped": raw_block_audit.get("skipped_blocks") or [],
        "profile_block_rules_hash": block_lineage.get("profile_block_rules_hash"),
        "global_block_rules_hash": block_lineage.get("global_block_rules_hash"),
        "effective_block_rules_hash": block_lineage.get("effective_block_rules_hash"),
        "profile_id": block_lineage.get("profile_id"),
        "profile_version_id": block_lineage.get("profile_version_id"),
        "profile_config_hash": block_lineage.get("profile_config_hash"),
        "profile_rules_count": block_lineage.get("profile_rules_count"),
        "global_rules_count": block_lineage.get("global_rules_count"),
        "effective_rules_count": block_lineage.get("effective_rules_count"),
        "reason_codes": block_lineage.get("reason_codes") or [],
    }
    would_authorize = bool(base_eligible and signals["gate_passed"] and entry["gate_passed"])
    shadow_decision = "ALLOW" if would_authorize else "BLOCK"

    envelope_material = {
        "contract_version": CONTRACT_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "evaluated_at": _jsonable(evaluated_at),
        "symbol": asset.get("symbol"),
        "timeframe": profile.get("default_timeframe", "5m"),
        "profile_id": profile.get("id") or profile.get("profile_id"),
        "score": _jsonable(score),
        "score_context": _jsonable(score_context or {}),
        "indicators": _jsonable(asset.get("indicators") or {}),
        "signals": signals,
        "entry_triggers": entry,
        "block_rules": block_rules,
        "base_eligible": bool(base_eligible),
    }
    canonical = json.dumps(
        envelope_material, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    envelope_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    score_result = {
        "value": _jsonable(score),
        "context": _jsonable(score_context or {}),
        "evaluation_envelope_hash": envelope_hash,
    }
    signals["evaluation_envelope_hash"] = envelope_hash
    entry["evaluation_envelope_hash"] = envelope_hash
    block_rules["evaluation_envelope_hash"] = envelope_hash

    return {
        "contract_version": CONTRACT_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "promotion_status": PROMOTION_STATUS,
        "operational_effect": False,
        "human_approval_required": True,
        "evaluated_at": _jsonable(evaluated_at),
        "evaluation_envelope_hash": envelope_hash,
        "score": score_result,
        "base_eligible": bool(base_eligible),
        "signals": signals,
        "entry_triggers": entry,
        "block_rules": block_rules,
        "legacy_decision": legacy_decision,
        "shadow_decision": shadow_decision,
        "would_authorize": would_authorize,
        "decision_drift": legacy_decision != shadow_decision,
        "reason_codes": (
            [] if would_authorize else
            (["BASE_GATE_FAILED"] if not base_eligible else [])
            + (["BLOCK_RULES_MATCHED"] if block_rules["blocked"] else [])
            + (["SIGNALS_GATE_FAILED"] if not signals["gate_passed"] else [])
            + (["ENTRY_TRIGGERS_GATE_FAILED"] if not entry["gate_passed"] else [])
        ),
    }
