"""Read-only R6 contract audit; does not activate layers or rewrite history."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


def _indicators(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        value = node.get("indicator") or node.get("field")
        if isinstance(value, str) and value.strip():
            found.add(value.strip())
        for child in node.values():
            found.update(_indicators(child))
    elif isinstance(node, list):
        for child in node:
            found.update(_indicators(child))
    return found


def main() -> None:
    database_url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_PUBLIC_URL or DATABASE_URL is required")
    connection = psycopg2.connect(database_url, connect_timeout=15)
    connection.set_session(readonly=True, autocommit=True)
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT config_json->'scanner'->'multilayer_contract' AS contract
              FROM config_profiles
             WHERE config_type = 'spot_engine' AND is_active IS TRUE
             ORDER BY updated_at DESC NULLS LAST
             LIMIT 1
        """)
        config_row = cursor.fetchone() or {}
        contract = config_row.get("contract")

        cursor.execute("""
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'shadow_trades'
               AND column_name IN (
                   'rejected_by_layer', 'rejected_by_rule', 'layer_verdicts'
               )
             ORDER BY column_name
        """)
        shadow_columns = cursor.fetchall()

        cursor.execute("""
            SELECT indicator_id, alias_of, phenomenon, owning_layer,
                   timeframe, producer, source_family, is_blocking,
                   composed_inputs, contract_version
              FROM indicator_registry
             ORDER BY indicator_id
        """)
        registry = cursor.fetchall()
        cursor.execute("""
            SELECT id::text, name, config
              FROM profiles
             WHERE is_active IS TRUE
             ORDER BY id
        """)
        profiles = cursor.fetchall()
    connection.close()

    registry_ids = {row["indicator_id"] for row in registry}
    used: set[str] = set()
    for profile in profiles:
        config = profile.get("config") or {}
        for section in ("filters", "signals", "block_rules", "entry_triggers"):
            used.update(_indicators(config.get(section) or {}))
    missing_inputs = sorted({
        str(input_id)
        for row in registry
        for input_id in (row.get("composed_inputs") or [])
        if str(input_id) not in registry_ids
    })
    blocking_layers: dict[str, set[str]] = defaultdict(set)
    for row in registry:
        if row["is_blocking"]:
            blocking_layers[row["phenomenon"]].add(row["owning_layer"])
    duplicate_blocking = {
        key: sorted(value)
        for key, value in blocking_layers.items()
        if len(value) > 1
    }
    layers = (contract or {}).get("layers") or {}
    observational_off = bool(contract) and contract.get("enabled") is False and all(
        (layers.get(layer) or {}).get("observational_enabled") is False
        for layer in ("L1", "L2", "L3")
    )
    output = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "schema_columns": {
                "status": "PASS" if len(shadow_columns) == 3 else "FAIL",
                "value": shadow_columns,
            },
            "indicator_conservation": {
                "status": "PASS" if used <= registry_ids else "FAIL",
                "registered": len(registry_ids),
                "used_by_active_profiles": len(used),
                "unregistered": sorted(used - registry_ids),
            },
            "composed_indicator_inputs": {
                "status": "PASS" if not missing_inputs else "FAIL",
                "missing": missing_inputs,
            },
            "single_blocking_owner_per_phenomenon": {
                "status": "PASS" if not duplicate_blocking else "FAIL",
                "duplicates": duplicate_blocking,
            },
            "observational_decision_equivalence": {
                "status": "DEFINED_NOT_ACTIVE" if observational_off else "FAIL",
                "future_measurement": "legacy_decision versus multilayer_decision per event",
            },
            "decision_data_boundary": {
                "status": (
                    "DEFINED_NOT_APPLIED"
                    if contract and contract.get("decision_feature_valid_from") is None
                    else "FAIL"
                ),
                "contract_version": (
                    contract or {}
                ).get("decision_feature_contract_version"),
                "valid_from": (contract or {}).get("decision_feature_valid_from"),
            },
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
