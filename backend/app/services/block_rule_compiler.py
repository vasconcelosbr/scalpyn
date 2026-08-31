"""Versioned normalization for legacy flat block rules."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


CONTRACT_VERSION = "block_rule_audit_v2"
NORMALIZATION_VERSION = "block_rule_normalization_v1"


def compile_block_rule(
    rule: Mapping[str, Any], *, legacy_range_enabled: bool
) -> dict[str, Any]:
    configured = deepcopy(dict(rule))
    compiled = deepcopy(configured)
    normalization = "UNCHANGED"
    if (
        legacy_range_enabled
        and not compiled.get("conditions")
        and compiled.get("type") in (None, "")
        and compiled.get("min") is not None
        and compiled.get("max") is not None
    ):
        compiled["type"] = "range"
        normalization = "FLAT_MIN_MAX_TO_OUTSIDE_RANGE"
    return {
        "contract_version": CONTRACT_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "normalization": normalization,
        "configured": configured,
        "compiled": compiled,
        "operational_effect": bool(
            legacy_range_enabled and normalization != "UNCHANGED"
        ),
    }
