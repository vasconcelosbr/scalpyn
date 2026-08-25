"""Low-cardinality telemetry for the observational L3 gate v2 canary."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter  # type: ignore[import-untyped]

    _EVALUATIONS = Counter(
        "l3_gate_v2_evaluations_total",
        "Observational L3 gate v2 evaluations",
        ["legacy_decision", "shadow_decision", "drift"],
    )
    _SECTION_OUTCOMES = Counter(
        "l3_gate_v2_section_outcomes_total",
        "Observational L3 gate v2 section outcomes",
        ["section", "outcome"],
    )
    _CAPTURE_OUTCOMES = Counter(
        "l3_gate_v2_capture_total",
        "Durable capture outcomes for observational L3 gate v2 evaluations",
        ["outcome"],
    )
    _BLOCK_RULES_CONFIGURED = Counter(
        "l3_block_rules_configured_total",
        "Block rules present in effective L3 runtime contracts",
    )
    _BLOCK_RULES_EVALUATED = Counter(
        "l3_block_rules_evaluated_total",
        "Effective L3 block rules evaluated with valid inputs",
    )
    _BLOCK_RULES_MATCHED = Counter(
        "l3_block_rules_matched_total",
        "Effective L3 block rules that matched and vetoed entry",
    )
    _PROFILE_RULES_DROPPED = Counter(
        "l3_profile_rules_dropped_total",
        "Unsafe L3 runtime assemblies that lost profile block rules",
    )
    _BLOCK_CONFIG_CONFLICT = Counter(
        "l3_block_config_conflict_total",
        "L3 block-rule identity conflicts rejected fail-closed",
    )
except Exception as exc:  # pragma: no cover - optional in local tests
    _EVALUATIONS = None
    _SECTION_OUTCOMES = None
    _CAPTURE_OUTCOMES = None
    _BLOCK_RULES_CONFIGURED = None
    _BLOCK_RULES_EVALUATED = None
    _BLOCK_RULES_MATCHED = None
    _PROFILE_RULES_DROPPED = None
    _BLOCK_CONFIG_CONFLICT = None
    logger.debug("prometheus_client unavailable: %s", exc)


def observe_gate_v2(result: dict) -> None:
    if _EVALUATIONS is None or _SECTION_OUTCOMES is None:
        return
    try:
        _EVALUATIONS.labels(
            legacy_decision=str(result.get("legacy_decision") or "UNKNOWN"),
            shadow_decision=str(result.get("shadow_decision") or "UNKNOWN"),
            drift="true" if result.get("decision_drift") else "false",
        ).inc()
        for section in ("signals", "entry_triggers"):
            section_result = result.get(section) or {}
            outcome = "PASS" if section_result.get("gate_passed") else "FAIL"
            _SECTION_OUTCOMES.labels(section=section, outcome=outcome).inc()
    except Exception:
        logger.debug("failed to emit L3 gate v2 metrics", exc_info=True)


def observe_gate_capture(outcome: str) -> None:
    """Count durable-capture outcomes using a bounded label vocabulary."""

    if _CAPTURE_OUTCOMES is None:
        return
    allowed = {
        "inserted",
        "replayed",
        "invalid",
        "count_mismatch",
        "error",
        "decision_linked",
        "shadow_linked",
    }
    normalized = outcome if outcome in allowed else "error"
    try:
        _CAPTURE_OUTCOMES.labels(outcome=normalized).inc()
    except Exception:
        logger.debug("failed to emit L3 gate v2 capture metric", exc_info=True)


def observe_block_rules(result: dict) -> None:
    """Record rule volumes without high-cardinality labels."""

    if _BLOCK_RULES_CONFIGURED is None:
        return
    try:
        configured = result.get("evaluated") or []
        evaluated = [rule for rule in configured if rule.get("evaluated")]
        matched = result.get("matched") or []
        _BLOCK_RULES_CONFIGURED.inc(len(configured))
        _BLOCK_RULES_EVALUATED.inc(len(evaluated))
        _BLOCK_RULES_MATCHED.inc(len(matched))
    except Exception:
        logger.debug("failed to emit L3 block-rule metrics", exc_info=True)


def observe_block_config_failure(reason_code: str) -> None:
    try:
        if reason_code == "PROFILE_BLOCK_RULES_DROPPED":
            if _PROFILE_RULES_DROPPED is not None:
                _PROFILE_RULES_DROPPED.inc()
        elif reason_code == "BLOCK_RULE_CONFIG_CONFLICT":
            if _BLOCK_CONFIG_CONFLICT is not None:
                _BLOCK_CONFIG_CONFLICT.inc()
    except Exception:
        logger.debug("failed to emit L3 block-config failure metric", exc_info=True)
