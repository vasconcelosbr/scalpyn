from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.layer_context import L1DecisionContextV1
from app.services.indicator_registry_service import audit_indicator_registry
from app.services.multilayer_contract import (
    MULTILAYER_EXECUTION_CONTRACT_VERSION,
    build_layer_verdicts,
    build_multilayer_execution_contract,
    consolidate_multilayer_event,
    evaluate_layer_allowlist,
    read_execution_contract,
    require_prepared_multilayer_config,
    resolve_layer_timeframe,
)
from app.services.profile_execution_contract import build_execution_contract_snapshot
from app.services.profile_runtime_config import canonical_hash


ROOT = Path(__file__).parents[1]
REGISTRY_PATH = ROOT / "app" / "contracts" / "r6_indicator_registry_v1.json"
MIGRATION_PATH = ROOT / "alembic" / "versions" / "215_r6_multilayer_contracts.py"


def _legacy_snapshot(profile_id: str) -> dict:
    config = {
        "filters": {"logic": "AND", "conditions": []},
        "signals": {"logic": "AND", "conditions": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
    }
    return build_execution_contract_snapshot(
        profile_id=profile_id,
        profile_name=profile_id,
        profile_config=config,
        profile_version_id=f"version-{profile_id}",
        version_profile_id=profile_id,
        version_config=config,
        version_config_hash=canonical_hash(config),
        active_version_count=1,
    )


def test_multilayer_contract_keeps_homonymous_sections_under_each_layer():
    snapshots = {layer: _legacy_snapshot(layer) for layer in ("L1", "L2", "L3")}
    result = build_multilayer_execution_contract(
        layer_snapshots=snapshots,
        layer_default_timeframes={"L1": "1h", "L2": "15m", "L3": "5m"},
        valid_from="2026-09-05T00:00:00+00:00",
    )

    assert result["contract_version"] == MULTILAYER_EXECUTION_CONTRACT_VERSION
    assert set(result["layers"]) == {"L1", "L2", "L3"}
    assert "sections" not in result
    assert all(
        set(result["layers"][layer]["section_hashes"])
        == {"filters", "signals", "entry_triggers", "block_rules"}
        for layer in ("L1", "L2", "L3")
    )
    assert result["aggregate_hash"]


def test_historical_single_profile_contract_remains_readable():
    legacy = _legacy_snapshot("L3")
    assert read_execution_contract(legacy)["format"] == "LEGACY_SINGLE_PROFILE"


def test_layer_timeframe_precedence_is_condition_then_policy_then_layer_default():
    layer = {"default_timeframe": "1h"}
    assert resolve_layer_timeframe(
        condition={"timeframe": "5m"}, source_policy={"timeframe": "15m"}, layer_config=layer
    ) == {"timeframe": "5m", "resolved_from": "CONDITION"}
    assert resolve_layer_timeframe(
        condition={}, source_policy={"timeframe": "15m"}, layer_config=layer
    ) == {"timeframe": "15m", "resolved_from": "SOURCE_POLICY"}
    assert resolve_layer_timeframe(
        condition={}, source_policy={"timeframe": None}, layer_config=layer
    ) == {"timeframe": "1h", "resolved_from": "LAYER_DEFAULT"}


def test_outside_allowlist_is_explicit_report_only_without_operational_effect():
    result = evaluate_layer_allowlist(
        profile_id="outside",
        layer_config={"profile_allowlist": [], "outside_allowlist_policy": "REPORT_ONLY"},
    )
    assert result == {"status": "OUTSIDE_ALLOWLIST", "operational_effect": False}


def test_consolidation_v2_keeps_one_l3_candidate_and_no_suppressed_layers():
    result = consolidate_multilayer_event(
        event_identity={"symbol": "BTC_USDT", "direction": "SPOT"},
        verdicts={
            "L1": {"verdict": "REJECT", "rule": "INSUFFICIENT_HISTORY_FOR_EMA200"},
            "L2": {"verdict": "PASS", "rule": None},
            "L3": {"verdict": "PASS", "rule": None},
        },
    )
    assert result["candidate_layers"] == ["L3"]
    assert result["suppressed_layers"] == []
    assert result["rejected_by_layer"] == "L1"
    assert result["rejected_by_rule"] == "INSUFFICIENT_HISTORY_FOR_EMA200"


def test_no_rejection_uses_none_enum_value():
    result = build_layer_verdicts(
        {layer: {"verdict": "PASS", "rule": None} for layer in ("L1", "L2", "L3")}
    )
    assert result["rejected_by_layer"] == "NONE"


def test_l1_context_accepts_long_reason_and_rejects_strength_outside_unit_interval():
    payload = {
        "l1_trend_state": "TREND_STRONG",
        "l1_volatility_state": "HIGH",
        "l1_structure_event": "PULLBACK",
        "l1_direction": "UP",
        "l1_strength": 0.8,
        "l1_regime_since": datetime(2026, 9, 5, tzinfo=timezone.utc),
        "l1_previous_regime": "RANGING",
        "l1_timeframe": "1h",
        "l1_computed_at": datetime(2026, 9, 5, tzinfo=timezone.utc),
        "l1_candle_policy": "CLOSED_ONLY",
        "l1_source": "ohlcv",
        "l1_contract_version": "multilayer_decision_context_v1",
        "l1_verdict": "INSUFFICIENT_DATA",
        "l1_verdict_reason": "INSUFFICIENT_HISTORY_FOR_EMA200",
    }
    assert L1DecisionContextV1.model_validate(payload).l1_verdict_reason == payload["l1_verdict_reason"]
    with pytest.raises(ValidationError):
        L1DecisionContextV1.model_validate({**payload, "l1_strength": 1.1})


def test_prepared_config_fails_closed_when_missing_or_enabled():
    with pytest.raises(ValueError, match="NOT_MATERIALIZED"):
        require_prepared_multilayer_config({})
    with pytest.raises(ValueError, match="MUST_REMAIN_DISABLED"):
        require_prepared_multilayer_config(
            {"multilayer_contract": {"enabled": True}}
        )


def test_registry_has_one_owner_complete_compositions_and_no_cross_layer_blocking():
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    rows = document["indicators"]
    ids = [row["indicator_id"] for row in rows]
    assert len(ids) == len(set(ids))
    assert all(row["owning_layer"] in {"L1", "L2", "L3"} for row in rows)
    assert all(
        input_id in set(ids)
        for row in rows
        for input_id in row["composed_inputs"]
    )
    blocking_layers = {}
    for row in rows:
        if row["is_blocking"]:
            blocking_layers.setdefault(row["phenomenon"], set()).add(row["owning_layer"])
    assert all(len(layers) == 1 for layers in blocking_layers.values())
    aliases = {row["indicator_id"]: row["alias_of"] for row in rows}
    assert aliases["orderbook_pressure"] == "bid_ask_imbalance"
    assert aliases["buy_pressure"] == "taker_ratio"
    assert aliases["atr_percent"] == "atr_pct"
    producers = {row["indicator_id"]: row["producer"] for row in rows}
    assert producers["breakout_distance_pct"] is None
    assert producers["psar_trend"] is None


def test_registry_audit_reports_collapsed_alias_conditions_without_changing_rule():
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    profile = {
        "id": "p1",
        "name": "TEST",
        "config": {
            "filters": {},
            "signals": {},
            "entry_triggers": {},
            "block_rules": {
                "blocks": [{
                    "id": "book",
                    "name": "Book vendedor extremo",
                    "conditions": [
                        {"indicator": "orderbook_pressure"},
                        {"indicator": "bid_ask_imbalance"},
                    ],
                }]
            },
        },
    }
    result = audit_indicator_registry(document["indicators"], [profile])
    assert result["valid"] is True
    assert result["collapsed_rule_conditions"][0]["canonical_indicator"] == "bid_ask_imbalance"
    assert profile["config"]["block_rules"]["blocks"][0]["conditions"] == [
        {"indicator": "orderbook_pressure"},
        {"indicator": "bid_ask_imbalance"},
    ]


def test_migration_uses_unbounded_text_and_preserves_historical_nulls():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "rejected_by_layer TEXT NULL" in source
    assert "rejected_by_rule TEXT NULL" in source
    assert "layer_verdicts JSONB NULL" in source
    assert "VARCHAR(2)" not in source
    assert "UPDATE shadow_trades" not in source
