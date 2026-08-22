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
except Exception as exc:  # pragma: no cover - optional in local tests
    _EVALUATIONS = None
    _SECTION_OUTCOMES = None
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

