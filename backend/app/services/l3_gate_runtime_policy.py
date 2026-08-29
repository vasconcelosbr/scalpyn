"""Canonical, GUI-backed runtime policy for the L3 gate instrumentation.

The scanner injects one immutable snapshot into each profile evaluation.  The
snapshot is hashed and persisted in the gate envelope, which makes a toggle
change attributable without coupling it to a process restart or deployment.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping


POLICY_CONTRACT_VERSION = "l3_gate_runtime_policy_v1"
ENVELOPE_CONTRACT_VERSION = "l3_gate_evaluation_envelope_v3"

POLICY_FIELDS = (
    "l3_v3_contract_preserve",
    "l3_condition_status_capture",
    "l3_metrics_provenance",
    "l3_zero_is_value",
    "l3_block_and_skipped_policy",
    "l3_missing_indicator_policy",
)

DEFAULT_POLICY = {
    "l3_v3_contract_preserve": True,
    "l3_condition_status_capture": True,
    "l3_metrics_provenance": True,
    "l3_zero_is_value": True,
    "l3_block_and_skipped_policy": "legacy",
    "l3_missing_indicator_policy": "warn",
}

SUPPORTED_AND_SKIPPED_POLICIES = frozenset({"legacy", "not_satisfied"})
SUPPORTED_MISSING_INDICATOR_POLICIES = frozenset({"warn", "disable_rule"})

# Structural capability catalog, not a strategy threshold.  These two names
# are referenced by active profile rules but have no canonical producer in the
# current L3 evaluation object.
KNOWN_UNIMPLEMENTED_INDICATORS = frozenset(
    {"breakout_distance_pct", "psar_trend"}
)


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_policy_snapshot(
    source: Mapping[str, Any] | Any,
    *,
    source_name: str = "spot_engine.scanner",
) -> dict[str, Any]:
    """Validate and freeze the six L3 controls from a scanner config."""

    if hasattr(source, "model_dump"):
        values = source.model_dump()
    elif isinstance(source, Mapping):
        values = dict(source)
    else:
        values = {}

    configured = all(field in values for field in POLICY_FIELDS)
    controls = {
        field: deepcopy(values.get(field, DEFAULT_POLICY[field]))
        for field in POLICY_FIELDS
    }
    if controls["l3_block_and_skipped_policy"] not in SUPPORTED_AND_SKIPPED_POLICIES:
        raise ValueError("l3_block_and_skipped_policy_invalid")
    if controls["l3_missing_indicator_policy"] not in SUPPORTED_MISSING_INDICATOR_POLICIES:
        raise ValueError("l3_missing_indicator_policy_invalid")

    hash_material = {
        "contract_version": POLICY_CONTRACT_VERSION,
        **controls,
    }
    return {
        **hash_material,
        "config_hash": _canonical_hash(hash_material),
        "source": source_name,
        "configured": configured,
    }


def policy_from_profile(profile_config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read an injected policy or return an explicitly marked compatibility snapshot."""

    raw = (profile_config or {}).get("_l3_gate_runtime_policy")
    if isinstance(raw, Mapping):
        return build_policy_snapshot(raw, source_name=str(raw.get("source") or "injected"))
    snapshot = build_policy_snapshot({}, source_name="schema_default_compatibility")
    snapshot["configured"] = False
    return snapshot
