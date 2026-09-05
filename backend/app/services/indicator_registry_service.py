"""Read-only audits for the persisted R6 indicator ownership registry."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping


EXECUTION_SECTIONS = ("filters", "signals", "block_rules", "entry_triggers")


def _condition_indicators(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, Mapping):
        indicator = node.get("indicator") or node.get("field")
        if isinstance(indicator, str) and indicator.strip():
            found.append(indicator.strip())
        for value in node.values():
            found.extend(_condition_indicators(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_condition_indicators(value))
    return found


def audit_indicator_registry(
    registry_rows: Iterable[Mapping[str, Any]],
    profile_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    registry = {str(row["indicator_id"]): dict(row) for row in registry_rows}
    aliases = {
        indicator_id: str(row["alias_of"])
        for indicator_id, row in registry.items()
        if row.get("alias_of")
    }
    used: set[str] = set()
    collapsed: list[dict[str, Any]] = []
    for profile in profile_rows:
        config = profile.get("config") or {}
        for section in EXECUTION_SECTIONS:
            used.update(_condition_indicators(config.get(section) or {}))
        blocks = (config.get("block_rules") or {}).get("blocks") or []
        for index, block in enumerate(blocks):
            indicators = _condition_indicators(block)
            by_canonical: dict[str, list[str]] = defaultdict(list)
            for indicator in indicators:
                by_canonical[aliases.get(indicator, indicator)].append(indicator)
            for canonical, names in by_canonical.items():
                if len(names) > 1 and len(set(names)) > 1:
                    collapsed.append(
                        {
                            "profile_id": str(profile.get("id")),
                            "profile_name": profile.get("name"),
                            "section": "block_rules",
                            "rule_index": index,
                            "rule_id": block.get("id"),
                            "rule_name": block.get("name"),
                            "canonical_indicator": canonical,
                            "conditions": names,
                        }
                    )

    missing_inputs = sorted(
        {
            str(input_id)
            for row in registry.values()
            for input_id in (row.get("composed_inputs") or [])
            if str(input_id) not in registry
        }
    )
    blocking_layers: dict[str, set[str]] = defaultdict(set)
    for row in registry.values():
        if row.get("is_blocking"):
            blocking_layers[str(row["phenomenon"])].add(str(row["owning_layer"]))
    duplicated_blocking_phenomena = {
        phenomenon: sorted(layers)
        for phenomenon, layers in blocking_layers.items()
        if len(layers) > 1
    }
    return {
        "contract_version": next(
            (row.get("contract_version") for row in registry.values()), None
        ),
        "registry_count": len(registry),
        "profile_indicator_count": len(used),
        "unregistered_profile_indicators": sorted(used - set(registry)),
        "missing_composed_inputs": missing_inputs,
        "duplicated_blocking_phenomena": duplicated_blocking_phenomena,
        "collapsed_rule_conditions": collapsed,
        "valid": not (
            used - set(registry)
            or missing_inputs
            or duplicated_blocking_phenomena
        ),
    }
