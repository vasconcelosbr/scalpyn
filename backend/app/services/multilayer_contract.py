"""Pure R6 contracts.  Nothing in this module activates L1 or L2."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

from .profile_execution_contract import EXECUTION_SECTIONS
from .profile_runtime_config import canonical_hash


MULTILAYER_EXECUTION_CONTRACT_VERSION = "multilayer_profile_execution_contract_v2"
MULTILAYER_PROVENANCE_POLICY_VERSION = "multilayer_provenance_resolver_v1"
MULTILAYER_CONSOLIDATION_VERSION = "single_profile_per_symbol_v2"
MULTILAYER_DECISION_CONTEXT_VERSION = "multilayer_decision_context_v2"
LAYERS = ("L1", "L2", "L3")
LAYER_VERDICTS = {"PASS", "REJECT", "INSUFFICIENT_DATA", "UNAVAILABLE"}


def _iso(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def require_prepared_multilayer_config(scanner: Mapping[str, Any]) -> dict[str, Any]:
    """Reject missing/incomplete config instead of activating code defaults."""

    config = deepcopy(dict(scanner.get("multilayer_contract") or {}))
    if not config:
        raise ValueError("MULTILAYER_CONTRACT_NOT_MATERIALIZED")
    if config.get("enabled") is not False:
        raise ValueError("MULTILAYER_CONTRACT_MUST_REMAIN_DISABLED_IN_R6")
    if not config.get("execution_contract_valid_from"):
        raise ValueError("MULTILAYER_EXECUTION_VALID_FROM_MISSING")
    if not config.get("consolidation_valid_from"):
        raise ValueError("MULTILAYER_CONSOLIDATION_VALID_FROM_MISSING")
    if config.get("decision_feature_valid_from") is not None:
        raise ValueError("MULTILAYER_DECISION_BOUNDARY_MUST_NOT_BE_APPLIED_IN_R6")
    layers = config.get("layers") or {}
    if set(layers) != set(LAYERS):
        raise ValueError("MULTILAYER_LAYER_CONFIG_INCOMPLETE")
    for layer in LAYERS:
        layer_config = layers[layer] or {}
        if layer_config.get("observational_enabled") is not False:
            raise ValueError(f"{layer}_OBSERVATIONAL_MUST_REMAIN_DISABLED_IN_R6")
        if not layer_config.get("default_timeframe"):
            raise ValueError(f"{layer}_DEFAULT_TIMEFRAME_MISSING")
    for layer in ("L1", "L2"):
        if not layers[layer].get("profile_id"):
            raise ValueError(f"{layer}_PROFILE_ID_MISSING")
    return config


def require_shadow_multilayer_config(scanner: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the only operationally loadable MTF mode in this release."""
    config = deepcopy(dict(scanner.get("multilayer_contract") or {}))
    if not config:
        raise ValueError("MULTILAYER_CONTRACT_NOT_MATERIALIZED")
    if config.get("enabled") is not True or config.get("activation_mode") != "SHADOW":
        raise ValueError("MULTILAYER_SHADOW_NOT_ENABLED")
    if config.get("operational_effect") is not False:
        raise ValueError("MULTILAYER_OPERATIONAL_EFFECT_FORBIDDEN")
    if config.get("decision_feature_contract_version") != MULTILAYER_DECISION_CONTEXT_VERSION:
        raise ValueError("MULTILAYER_CONTEXT_VERSION_UNKNOWN")
    layers = config.get("layers") or {}
    if set(layers) != set(LAYERS):
        raise ValueError("MULTILAYER_LAYER_CONFIG_INCOMPLETE")
    expected_timeframes = {"L1": "1h", "L2": "15m", "L3": "5m"}
    for layer, expected_timeframe in expected_timeframes.items():
        item = layers.get(layer) or {}
        if item.get("observational_enabled") is not True:
            raise ValueError(f"{layer}_OBSERVATIONAL_NOT_ENABLED")
        if item.get("default_timeframe") != expected_timeframe:
            raise ValueError(f"{layer}_TIMEFRAME_MISMATCH")
        if item.get("validity_margin_seconds") is None:
            raise ValueError(f"{layer}_VALIDITY_MARGIN_CONFIG_REQUIRED")
        policies = item.get("source_policies") or {}
        ohlcv = policies.get("ohlcv") or {}
        if ohlcv.get("candle_policy") != "CLOSED_ONLY":
            raise ValueError(f"{layer}_CLOSED_ONLY_REQUIRED")
        if not ohlcv.get("allowed_source_providers") or not ohlcv.get("provider_policy_id"):
            raise ValueError(f"{layer}_SOURCE_POLICY_INCOMPLETE")
    for layer in ("L1", "L2"):
        item = layers[layer]
        for field in ("profile_id", "profile_version_id", "profile_config_hash"):
            if not item.get(field):
                raise ValueError(f"{layer}_{field.upper()}_MISSING")
    return config


def build_multilayer_execution_contract(
    *,
    layer_snapshots: Mapping[str, Mapping[str, Any]],
    layer_default_timeframes: Mapping[str, str],
    valid_from: datetime | str,
) -> dict[str, Any]:
    """Wrap three legacy child snapshots without flattening equal sections."""

    if set(layer_snapshots) != set(LAYERS):
        raise ValueError("layer_snapshots must contain exactly L1, L2 and L3")
    if set(layer_default_timeframes) != set(LAYERS):
        raise ValueError("layer_default_timeframes must contain exactly L1, L2 and L3")

    children: dict[str, Any] = {}
    child_hashes: dict[str, str] = {}
    for layer in LAYERS:
        snapshot = deepcopy(dict(layer_snapshots[layer]))
        sections = snapshot.get("sections") or {}
        if set(sections) != set(EXECUTION_SECTIONS):
            raise ValueError(f"{layer} execution snapshot has incomplete sections")
        section_hashes = {
            section: (sections[section] or {}).get("runtime_hash")
            for section in EXECUTION_SECTIONS
        }
        child_material = {
            "layer": layer,
            "default_timeframe": layer_default_timeframes[layer],
            "profile_id": snapshot.get("profile_id"),
            "profile_version_id": snapshot.get("profile_version_id"),
            "section_hashes": section_hashes,
            "legacy_contract_version": snapshot.get("contract_version"),
        }
        child_hash = canonical_hash(child_material)
        children[layer] = {
            **child_material,
            "contract_hash": child_hash,
            "contract": snapshot,
        }
        child_hashes[layer] = child_hash

    valid_from_value = _iso(valid_from)
    aggregate_material = {
        "contract_version": MULTILAYER_EXECUTION_CONTRACT_VERSION,
        "valid_from": valid_from_value,
        "child_hashes": child_hashes,
    }
    return {
        **aggregate_material,
        "aggregate_hash": canonical_hash(aggregate_material),
        "layers": children,
    }


def read_execution_contract(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Read both the historical single-profile envelope and R6 v2."""

    version = snapshot.get("contract_version")
    if version == "l3_profile_execution_contract_v1":
        return {"format": "LEGACY_SINGLE_PROFILE", "contract": deepcopy(dict(snapshot))}
    if version == MULTILAYER_EXECUTION_CONTRACT_VERSION:
        if set(snapshot.get("layers") or {}) != set(LAYERS):
            raise ValueError("multilayer execution contract has incomplete layers")
        contract = deepcopy(dict(snapshot))
        expected_aggregate = contract.get("aggregate_hash")
        aggregate_material = {
            "contract_version": contract.get("contract_version"),
            "valid_from": contract.get("valid_from"),
            "child_hashes": {
                layer: (contract["layers"][layer] or {}).get("contract_hash")
                for layer in LAYERS
            },
        }
        if expected_aggregate != canonical_hash(aggregate_material):
            raise ValueError("MULTILAYER_AGGREGATE_HASH_INVALID")
        for layer in LAYERS:
            child = contract["layers"][layer] or {}
            child_material = {
                key: child.get(key)
                for key in (
                    "layer", "default_timeframe", "profile_id",
                    "profile_version_id", "section_hashes",
                    "legacy_contract_version",
                )
            }
            if child.get("contract_hash") != canonical_hash(child_material):
                raise ValueError(f"{layer}_CONTRACT_HASH_INVALID")
        return {"format": "MULTILAYER", "contract": contract}
    raise ValueError(f"unsupported execution contract version: {version}")


def resolve_layer_timeframe(
    *,
    condition: Mapping[str, Any],
    source_policy: Mapping[str, Any],
    layer_config: Mapping[str, Any],
) -> dict[str, str]:
    """Resolve condition -> per-layer source policy -> per-layer default."""

    if condition.get("timeframe"):
        return {"timeframe": str(condition["timeframe"]), "resolved_from": "CONDITION"}
    if source_policy.get("timeframe"):
        return {"timeframe": str(source_policy["timeframe"]), "resolved_from": "SOURCE_POLICY"}
    if layer_config.get("default_timeframe"):
        return {
            "timeframe": str(layer_config["default_timeframe"]),
            "resolved_from": "LAYER_DEFAULT",
        }
    raise ValueError("LAYER_DEFAULT_TIMEFRAME_MISSING")


def evaluate_layer_allowlist(
    *, profile_id: str, layer_config: Mapping[str, Any]
) -> dict[str, Any]:
    allowlist = {str(item) for item in (layer_config.get("profile_allowlist") or [])}
    if profile_id in allowlist:
        return {"status": "ALLOWLISTED", "operational_effect": False}
    policy = layer_config.get("outside_allowlist_policy")
    if policy == "REPORT_ONLY":
        return {"status": "OUTSIDE_ALLOWLIST", "operational_effect": False}
    if policy == "REJECT_CONFIGURATION":
        raise ValueError("PROFILE_OUTSIDE_LAYER_ALLOWLIST")
    raise ValueError("OUTSIDE_ALLOWLIST_POLICY_MISSING")


def build_layer_verdicts(verdicts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if set(verdicts) != set(LAYERS):
        raise ValueError("verdicts must contain exactly L1, L2 and L3")
    normalized: dict[str, Any] = {}
    rejected_by_layer = "NONE"
    rejected_by_rule = None
    for layer in LAYERS:
        record = deepcopy(dict(verdicts[layer]))
        verdict = record.get("verdict")
        if verdict not in LAYER_VERDICTS:
            raise ValueError(f"invalid {layer} verdict: {verdict}")
        normalized[layer] = record
        if rejected_by_layer == "NONE" and verdict == "REJECT":
            rejected_by_layer = layer
            rejected_by_rule = record.get("rule")
    return {
        "rejected_by_layer": rejected_by_layer,
        "rejected_by_rule": rejected_by_rule,
        "layer_verdicts": normalized,
    }


def consolidate_multilayer_event(
    *, event_identity: Mapping[str, Any], verdicts: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """One L3 candidate/event with internal L1/L2 observations and no SUPPRESSED rows."""

    layer_result = build_layer_verdicts(verdicts)
    material = {
        "rule_version": MULTILAYER_CONSOLIDATION_VERSION,
        "event_identity": deepcopy(dict(event_identity)),
        **layer_result,
    }
    return {
        **material,
        "event_hash": canonical_hash(material),
        "candidate_layers": ["L3"],
        "suppressed_layers": [],
    }
