from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.services.l3_authorization_contract_v3 import (
    build_authorization_contract,
    build_feature_dependency_audit,
)


NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def candidate(indicator="taker_ratio", **overrides):
    return {
        "indicator": indicator, "actual": 0.7,
        "market_scope": {"exchange": "gate_io", "market_type": "spot", "normalized_symbol": "SUI_USDT"},
        "source": "live_trade_flow", "source_provider": "gate_trades_ws_spot",
        "provider_policy_id": "flow", "window_seconds": 60,
        "source_timestamp": NOW, "computed_at": NOW, "available_at": NOW,
        "age_seconds": 0, "stale": False, **overrides,
    }


def contract(registry):
    condition = {
        "field": "taker_ratio", "operator": ">=", "value": 0.52,
        "source": "live_trade_flow", "source_provider": "gate_trades_ws_spot",
        "provider_policy_id": "flow", "window_seconds": 60,
        "max_age_seconds": 60, "required": True,
    }
    profile = {
        "default_timeframe": "5m", "filters": {"conditions": []},
        "signals": {"conditions": [condition]}, "entry_triggers": {"conditions": []},
        "block_rules": {"blocks": []}, "scoring": {"enabled": False},
    }
    return build_authorization_contract(
        asset={"symbol": "SUI_USDT", "indicators": {"taker_ratio": 0.99}},
        profile_config=profile, legacy_decision="ALLOW", evaluated_at=NOW,
        profile_id="fixture", profile_name="fixture", profile_version=NOW,
        feature_registry=registry,
    )


def test_dependency_audit_requires_same_observation_and_value_for_alias():
    registry = [candidate(), candidate("buy_pressure"), candidate("volume_delta", actual=120)]
    before = deepcopy(registry)
    audit = build_feature_dependency_audit(registry)
    assert registry == before
    assert audit["operational_effect"] is False
    assert audit["scoring_effect"] is False
    group, = audit["groups"]
    assert group["statistical_independence"] == "NOT_ASSESSED"
    assert group["members"][1]["alias_equivalence"] == "SAME_OBSERVATION_AND_VALUE"
    assert group["members"][2]["formula_alias_of"] is None


@pytest.mark.parametrize("overrides", [
    {"window_seconds": 300}, {"actual": 0.6},
    {"source_timestamp": NOW - timedelta(seconds=1)},
    {"market_scope": {"exchange": "gate_io", "market_type": "spot", "normalized_symbol": "BTC_USDT"}},
])
def test_alias_name_does_not_prove_equal_observations(overrides):
    audit = build_feature_dependency_audit([candidate(), candidate("buy_pressure", **overrides)])
    member = next(m for g in audit["groups"] for m in g["members"] if m["indicator"] == "buy_pressure")
    assert member["alias_equivalence"] == "NOT_ESTABLISHED"


@pytest.mark.parametrize("missing", ["source_timestamp", "computed_at", "available_at", "market_scope"])
def test_unknown_clocks_or_market_are_not_grouped_as_a_shared_observation(missing):
    audit = build_feature_dependency_audit([candidate(**{missing: None}), candidate("buy_pressure", **{missing: None})])
    assert len(audit["groups"]) == 2
    assert all(g["observation_hash"] is None for g in audit["groups"])


def test_valid_live_identity_keeps_authority_and_score_unchanged():
    result = contract([candidate(), candidate("buy_pressure")])
    assert result["authorization_status"] == "ALLOW"
    assert result["final_decision"] == "ALLOW"
    assert result["operational_effect"] is False
    assert result["score_audit"] == {"enabled": False}
    assert result["profile_lineage"]["rules_snapshot"]["signals"]["conditions"][0]["window_seconds"] == 60


def test_persisted_registry_preserves_scheduler_group_on_replay():
    result = contract([candidate(scheduler_group="microstructure")])
    assert result["feature_registry"][0]["scheduler_group"] == "microstructure"
    again = contract(result["feature_registry"])
    assert again["feature_registry"] == result["feature_registry"]
    assert again["authorization_status"] == result["authorization_status"]


def test_persisted_derived_candidate_retains_dependency_lineage():
    derived = candidate("breakout_distance_pct", reference_window="15m", dependencies=["parent-hash"])
    row = contract([derived])["feature_registry"][0]
    assert row["dependencies"] == ["parent-hash"]
    assert row["reference_window"] == "15m"
    assert row["parameters"]["reference_window"] == "15m"


@pytest.mark.parametrize("overrides,reason", [
    ({"window_seconds": 300}, "WINDOW_MISMATCH"),
    ({"fallback_used": True}, "FALLBACK_FORBIDDEN"),
    ({"partial_window": True}, "PARTIAL_WINDOW"),
    ({"available_at": NOW + timedelta(seconds=1)}, "AVAILABLE_AT_IN_FUTURE"),
    ({"source_timestamp": None}, "SOURCE_TIMESTAMP_MISSING"),
    ({"age_seconds": 61}, "FEATURE_TTL_EXPIRED"),
])
def test_invalid_flow_is_not_replaced_by_flat_indicator_or_profile_timeframe(overrides, reason):
    result = contract([candidate(**overrides)])
    assert result["authorization_status"] == "CONTRACT_REJECT"
    assert reason in str(result["signals_audit"])
    # Observational contract remains observational; no silent SHADOW -> ENFORCE.
    assert result["final_decision"] == "ALLOW"
    assert result["operational_effect"] is False
