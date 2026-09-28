"""Offline replay of a frozen L3 decision export. No DB, orders or profile writes.

Input: {"shadow": {...}, "decision": {"metrics": {...}}}, exported read-only.
Uses only the original decision's registry, profile, clocks and runtime policy.
This re-evaluates persisted inputs; it does not reconstruct original trades.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from app.services.l3_authorization_contract_v3 import build_authorization_contract, canonical_hash


def replay(source: dict) -> dict:
    metrics = source["decision"]["metrics"]
    original = metrics["l3_authorization_contract_v3"]
    gate = metrics["l3_gate_v2"]
    lineage = original["profile_lineage"]
    watchlist = original.get("watchlist_lineage") or {}
    profile = deepcopy(lineage["rules_snapshot"])
    if original.get("profile_execution_contract"):
        profile["_execution_contract"] = deepcopy(original["profile_execution_contract"])
    global_section = gate.get("global_entry_triggers") or {}
    if global_section.get("global_trigger_count"):
        raise ValueError("Historical global trigger configuration required; evaluation traces cannot replace rules.")
    rebuilt = build_authorization_contract(
        asset={"symbol": source["decision"]["symbol"]},
        profile_config=profile,
        legacy_decision=original["legacy_decision"],
        evaluated_at=datetime.fromisoformat(original["evaluated_at"].replace("Z", "+00:00")),
        profile_id=lineage["profile_id"], profile_name=lineage["profile_name"],
        profile_version=lineage["profile_version"],
        watchlist_id=watchlist.get("watchlist_id"), watchlist_name=watchlist.get("watchlist_name"),
        watchlist_level=watchlist.get("watchlist_level"), source_watchlist_id=watchlist.get("source_watchlist_id"),
        market_type=original["market_scope"]["market_type"],
        runtime_policy=gate["runtime_policy"], gate_evaluation=gate,
        feature_registry=original["feature_registry"],
    )
    def behavioral(value):
        if isinstance(value, dict):
            normalized = dict(value)
            # The canonical contract folds reference_window into parameters.
            # Compare that documented normalization, not JSON placement.
            if "reference_window" in normalized and ("indicator" in normalized or "source" in normalized):
                normalized["parameters"] = {**(normalized.get("parameters") or {}),
                                            "reference_window": normalized.pop("reference_window")}
            return {key: behavioral(child) for key, child in normalized.items()
                    if key not in {"evaluation_envelope_hash", "hashes", "feature_dependency_audit"}}
        if isinstance(value, list):
            return [behavioral(child) for child in value]
        return value
    comparison = {}
    for key in ("authorization_status", "contract_technical_decision", "legacy_decision",
                "operational_effect", "score_audit", "feature_evaluations", "sections", "feature_registry"):
        comparison[key] = behavioral(original.get(key)) == behavioral(rebuilt.get(key))
    tracked = {"taker_ratio", "buy_pressure", "volume_delta", "taker_buy_volume", "taker_sell_volume",
               "volume_spike", "orderbook_pressure", "bid_ask_imbalance"}
    return {
        "source_file_hash": canonical_hash(source),
        "shadow_trade_id": source["shadow"]["id"],
        "profile_id": lineage["profile_id"], "profile_version": lineage["profile_version"],
        "decision_at": original["evaluated_at"],
        "entry_at": source["shadow"]["entry_timestamp"],
        "replay_kind": "PERSISTED_INPUTS_ONLY",
        "raw_trade_recalculation": "NOT_AVAILABLE_IN_EXPORT",
        "original_authorization": original["authorization_status"],
        "replayed_authorization": rebuilt["authorization_status"],
        "behavioral_comparison": comparison,
        "all_compared_fields_equal": all(comparison.values()),
        "registry_byte_shape_equal": original["feature_registry"] == rebuilt["feature_registry"],
        "comparison_normalization": "reference_window folded into parameters; derived envelope hashes excluded",
        "score_trace": gate.get("score"),
        "historical_rule_definitions_available": bool((source.get("historical_score_version") or {}).get("rules")),
        "live_flow": metrics.get("_l3_live_order_flow_snapshot"),
        "feature_evaluations": [v for v in rebuilt["feature_evaluations"] if v.get("indicator") in tracked],
        "dependency_audit": rebuilt["feature_dependency_audit"],
        "replayed_sections": rebuilt["sections"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "replay_kind", "original_authorization", "replayed_authorization",
        "behavioral_comparison", "all_compared_fields_equal", "historical_rule_definitions_available")}, indent=2))


if __name__ == "__main__":
    main()
