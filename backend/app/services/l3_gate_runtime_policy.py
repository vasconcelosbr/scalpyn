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
    "l3_v3_provenance_resolver",
)

DEFAULT_PROVENANCE_RESOLVER = {
    "enabled": False,
    "profile_allowlist": [],
    "policy_version": "l3_v3_provenance_resolver_v1",
    "source_policies": {
        source: {
            "allowed_source_providers": [],
            "provider_policy_id": None,
            "max_age_seconds": None,
            "timeframe": None,
            "window_seconds": None,
            "snapshot": None,
            "candle_policy": None,
        }
        for source in (
            "ohlcv", "live_trade_flow", "live_order_book", "decision_context"
        )
    },
}

DEFAULT_POLICY = {
    "l3_v3_contract_preserve": True,
    "l3_condition_status_capture": True,
    "l3_metrics_provenance": True,
    "l3_zero_is_value": True,
    "l3_block_and_skipped_policy": "legacy",
    "l3_missing_indicator_policy": "warn",
    "l3_v3_provenance_resolver": DEFAULT_PROVENANCE_RESOLVER,
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


def _normalize_provenance_resolver(value: Any) -> dict[str, Any]:
    raw = deepcopy(dict(value)) if isinstance(value, Mapping) else {}
    normalized = deepcopy(DEFAULT_PROVENANCE_RESOLVER)
    normalized.update(raw)
    if normalized.get("policy_version") != "l3_v3_provenance_resolver_v1":
        raise ValueError("l3_v3_provenance_policy_version_invalid")
    allowlist = normalized.get("profile_allowlist")
    if not isinstance(allowlist, list):
        raise ValueError("l3_v3_provenance_profile_allowlist_invalid")
    normalized["profile_allowlist"] = [str(item) for item in allowlist]
    policies = normalized.get("source_policies")
    if not isinstance(policies, Mapping):
        raise ValueError("l3_v3_provenance_source_policies_invalid")
    unsupported = sorted(
        set(policies) - {"ohlcv", "live_trade_flow", "live_order_book", "decision_context"}
    )
    if unsupported:
        raise ValueError(
            "l3_v3_provenance_source_policy_unsupported:" + ",".join(unsupported)
        )
    normalized["source_policies"] = deepcopy(dict(policies))
    normalized["enabled"] = bool(normalized.get("enabled"))
    return normalized


def build_policy_snapshot(
    source: Mapping[str, Any] | Any,
    *,
    source_name: str = "spot_engine.scanner",
) -> dict[str, Any]:
    """Validate and freeze the governed L3 controls from a scanner config."""

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
    controls["l3_v3_provenance_resolver"] = _normalize_provenance_resolver(
        controls.get("l3_v3_provenance_resolver")
    )
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
