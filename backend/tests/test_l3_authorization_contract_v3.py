from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.l3_authorization_contract_v3 import (
    build_authorization_contract,
    build_feature_registry,
    validate_profile_contract,
)
from app.api.shadow_trade_reports import _classify_legacy_section_hashes
from app.tasks.pipeline_scan import _ml_gate_audit_payload, _ml_gate_should_block


NOW = datetime(2026, 8, 25, 4, 44, 44, tzinfo=timezone.utc)


def _asset(*, window=300, symbol="ASTER_USDT", rsi_timeframe="5m"):
    merged = SimpleNamespace(candidates=[
        {
            "indicator": "taker_ratio",
            "actual": 0.21,
            "source": "gate_candles",
            "source_provider": "gate_io_candles",
            "provider_policy_id": "provider-policy-v1",
            "timeframe": "5m",
            "period": None,
            "source_timestamp": NOW,
            "age_seconds": 1,
            "stale": False,
        },
        {
            "indicator": "rsi",
            "actual": 58.7,
            "source": "gate_candles",
            "source_provider": "gate_io_candles",
            "provider_policy_id": "provider-policy-v1",
            "timeframe": rsi_timeframe,
            "period": 14,
            "candle_policy": "CLOSED_ONLY",
            "candle_closed": True,
            "source_timestamp": NOW,
            "computed_at": NOW,
            "available_at": NOW,
            "age_seconds": 1,
            "stale": False,
        },
    ])
    return {
        "symbol": symbol,
        "_merged_indicators": merged,
        # Legacy flat value may still contain a candle/previous overwrite. V3
        # ignores it and resolves only from the namespaced registry.
        "indicators": {"taker_ratio": 0.21, "rsi": 58.7},
        "_l3_live_order_flow_snapshot": {
            "values": {"taker_ratio": 0.576241},
            "meta": {
                "source_provider": "gate_io_trades_ws",
                "provider_policy_id": "provider-policy-v1",
                "window_seconds": window,
                "source_timestamp": "2026-08-25T04:44:40Z",
                "age_seconds": 4,
                "stale": False,
            },
        },
    }


def test_same_logical_name_keeps_ohlcv_and_live_candidates_separate():
    registry = build_feature_registry(_asset(), evaluated_at=NOW)
    taker = [c for c in registry if c["indicator"] == "taker_ratio"]
    assert {c["source"] for c in taker} == {"ohlcv", "live_trade_flow"}
    assert {c["actual"] for c in taker} == {0.21, 0.576241}


def test_exact_live_window_and_ohlcv_timeframe_period_are_resolved():
    profile = {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "signals": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
        "entry_triggers": {
            "logic": "AND",
            "conditions": [
                {
                    "id": "live-taker",
                    "indicator": "taker_ratio",
                    "source": "live_order_flow",
                    "source_provider": "gate_io_trades_ws",
                    "provider_policy_id": "provider-policy-v1",
                    "window_seconds": 300,
                    "max_age_seconds": 30,
                    "operator": ">=",
                    "value": 0.535,
                    "required": True,
                },
                {
                    "id": "rsi-5m",
                    "indicator": "rsi",
                    "source": "ohlcv",
                    "source_provider": "gate_io_candles",
                    "provider_policy_id": "provider-policy-v1",
                    "timeframe": "5m",
                    "period": 14,
                    "candle_policy": "CLOSED_ONLY",
                    "max_age_seconds": 30,
                    "operator": "between",
                    "min": 48,
                    "max": 67,
                    "required": True,
                },
            ],
        },
    }
    result = build_authorization_contract(
        asset=_asset(), profile_config=profile, legacy_decision="ALLOW",
        evaluated_at=NOW, profile_id="profile-1", profile_name="Profile 1",
        profile_version=NOW, watchlist_id="watchlist-1",
    )
    conditions = result["sections"]["entry_triggers"]["conditions"]
    assert [c["status"] for c in conditions] == ["PASS", "PASS"]
    assert conditions[0]["actual"] == 0.576241
    assert conditions[0]["condition_contract_hash"] == conditions[0]["resolved_feature_hash"]
    assert result["authorization_status"] == "ALLOW"
    assert result["final_decision"] == "ALLOW"
    assert result["operational_effect"] is False
    section_hashes = {
        section["evaluation_envelope_hash"]
        for section in result["sections"].values()
    }
    assert section_hashes == {result["evaluation_envelope_hash"]}


@pytest.mark.parametrize("symbol", ["ASTER_USDT", "ADA_USDT", "INJ_USDT"])
def test_required_wrong_live_window_contract_rejects_without_candle_fallback(symbol):
    profile = {
        "entry_triggers": {
            "conditions": [{
                "indicator": "taker_ratio",
                "source": "live_order_flow",
                "source_provider": "gate_io_trades_ws",
                "provider_policy_id": "provider-policy-v1",
                "window_seconds": 300,
                "max_age_seconds": 30,
                "operator": ">=",
                "value": 0.2,
                "required": True,
            }]
        }
    }
    result = build_authorization_contract(
        asset=_asset(window=60, symbol=symbol), profile_config=profile,
        legacy_decision="ALLOW", evaluated_at=NOW,
    )
    condition = result["sections"]["entry_triggers"]["conditions"][0]
    assert condition["status"] == "CONTRACT_REJECT"
    assert condition["reason_codes"] == ["WINDOW_MISMATCH"]
    assert result["authorization_status"] == "CONTRACT_REJECT"
    assert result["contract_technical_decision"] == "BLOCK"
    # SHADOW does not change current authority.
    assert result["final_decision"] == "ALLOW"


def test_required_60_second_contract_rejects_300_second_candidate():
    profile = {
        "entry_triggers": {"conditions": [{
            "indicator": "taker_ratio",
            "source": "live_trade_flow",
            "source_provider": "gate_io_trades_ws",
            "provider_policy_id": "provider-policy-v1",
            "window_seconds": 60,
            "max_age_seconds": 30,
            "operator": ">=",
            "value": 0.2,
            "required": True,
        }]},
    }
    result = build_authorization_contract(
        asset=_asset(window=300, symbol="INJ_USDT"),
        profile_config=profile,
        legacy_decision="ALLOW",
        evaluated_at=NOW,
    )
    condition = result["sections"]["entry_triggers"]["conditions"][0]
    assert condition["status"] == "CONTRACT_REJECT"
    assert condition["reason_codes"] == ["WINDOW_MISMATCH"]


def test_required_ohlcv_timeframe_mismatch_contract_rejects():
    profile = {
        "default_timeframe": "5m",
        "entry_triggers": {"conditions": [{
            "indicator": "rsi", "source": "ohlcv", "timeframe": "5m",
            "source_provider": "gate_io_candles",
            "provider_policy_id": "provider-policy-v1", "max_age_seconds": 30,
            "period": 14, "candle_policy": "CLOSED_ONLY", "operator": "between",
            "min": 48, "max": 67, "required": True,
        }]},
    }
    result = build_authorization_contract(
        asset=_asset(rsi_timeframe="15m"), profile_config=profile,
        legacy_decision="ALLOW", evaluated_at=NOW,
    )
    condition = result["sections"]["entry_triggers"]["conditions"][0]
    assert condition["status"] == "CONTRACT_REJECT"
    assert condition["reason_codes"] == ["TIMEFRAME_MISMATCH"]


def test_boolean_short_circuit_marks_remaining_condition_not_needed():
    profile = {
        "entry_triggers": {
            "logic": "AND",
            "conditions": [
                {
                    "indicator": "rsi", "source": "ohlcv", "timeframe": "5m",
                    "source_provider": "gate_io_candles",
                    "provider_policy_id": "provider-policy-v1", "max_age_seconds": 30,
                    "period": 14, "candle_policy": "CLOSED_ONLY", "operator": ">", "value": 90,
                    "required": True,
                },
                {
                    "indicator": "taker_ratio", "source": "live_order_flow",
                    "source_provider": "gate_io_trades_ws",
                    "provider_policy_id": "provider-policy-v1", "max_age_seconds": 30,
                    "window_seconds": 300, "operator": ">", "value": 0.1,
                    "required": True,
                },
            ],
        },
    }
    result = build_authorization_contract(
        asset=_asset(), profile_config=profile,
        legacy_decision="BLOCK", evaluated_at=NOW,
    )
    statuses = [
        c["status"] for c in result["sections"]["entry_triggers"]["conditions"]
    ]
    assert statuses == ["FAIL", "NOT_NEEDED_FOR_BOOLEAN_RESULT"]


def test_ingress_validator_requires_source_and_window_identity():
    errors = validate_profile_contract({
        "default_timeframe": "5m",
        "entry_triggers": {"conditions": [
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.5},
            {
                "indicator": "volume_delta", "source": "live_order_flow",
                "operator": ">", "value": 0,
            },
        ]},
    })
    codes = [e["code"] for e in errors]
    assert codes[0] == "SOURCE_REQUIRED"
    assert "WINDOW_SECONDS_REQUIRED" in codes
    assert "SOURCE_PROVIDER_REQUIRED" in codes
    assert "PROVIDER_POLICY_REQUIRED" in codes


def test_ada_breakout_import_without_reference_window_is_rejected():
    errors = validate_profile_contract({
        "default_timeframe": "5m",
        "entry_triggers": {"conditions": [{
            "indicator": "breakout_distance_pct", "source": "ohlcv",
            "source_provider": "gate_io_candles",
            "provider_policy_id": "provider-policy-v1", "max_age_seconds": 30,
            "timeframe": "5m", "candle_policy": "CLOSED_ONLY",
            "operator": ">=", "value": 0, "required": True,
        }]},
    })
    assert [e["code"] for e in errors] == ["REFERENCE_WINDOW_REQUIRED"]


def test_same_value_from_other_market_scope_is_rejected():
    asset = _asset()
    asset["_merged_indicators"].candidates[1]["market_scope"] = {
        "exchange": "gate_io", "market_type": "futures",
        "normalized_symbol": "ASTER_USDT",
    }
    profile = {"entry_triggers": {"conditions": [{
        "indicator": "rsi", "source": "ohlcv", "timeframe": "5m",
        "source_provider": "gate_io_candles",
        "provider_policy_id": "provider-policy-v1", "max_age_seconds": 30,
        "period": 14, "candle_policy": "CLOSED_ONLY", "operator": ">", "value": 1,
        "required": True,
    }]}}
    result = build_authorization_contract(
        asset=asset, profile_config=profile, legacy_decision="ALLOW",
        evaluated_at=NOW, market_type="spot",
    )
    condition = result["sections"]["entry_triggers"]["conditions"][0]
    assert condition["reason_codes"] == ["MARKET_SCOPE_MISMATCH"]


def test_same_value_from_other_exchange_is_rejected():
    asset = _asset()
    asset["_merged_indicators"].candidates[1]["market_scope"] = {
        "exchange": "other_exchange",
        "market_type": "spot",
        "normalized_symbol": "ASTER_USDT",
    }
    result = build_authorization_contract(
        asset=asset,
        profile_config=_single_rsi_profile(),
        legacy_decision="ALLOW",
        evaluated_at=NOW,
        profile_id="profile-1",
        profile_name="Profile 1",
        profile_version=NOW,
    )
    condition = result["sections"]["entry_triggers"]["conditions"][0]
    assert condition["reason_codes"] == ["MARKET_SCOPE_MISMATCH"]


def _single_rsi_profile(**overrides):
    condition = {
        "indicator": "rsi",
        "source": "ohlcv",
        "source_provider": "gate_io_candles",
        "provider_policy_id": "provider-policy-v1",
        "timeframe": "5m",
        "period": 14,
        "candle_policy": "CLOSED_ONLY",
        "max_age_seconds": 30,
        "operator": ">",
        "value": 1,
        "required": True,
    }
    condition.update(overrides)
    return {
        "filters": {"logic": "AND", "conditions": []},
        "signals": {"logic": "AND", "conditions": []},
        "entry_triggers": {"logic": "AND", "conditions": [condition]},
        "block_rules": {"blocks": []},
    }


def test_required_ttl_expiry_rejects_even_when_candidate_is_not_preflagged_stale():
    asset = _asset()
    asset["_merged_indicators"].candidates[1]["age_seconds"] = 31
    result = build_authorization_contract(
        asset=asset,
        profile_config=_single_rsi_profile(),
        legacy_decision="ALLOW",
        evaluated_at=NOW,
        profile_id="profile-1",
        profile_name="Profile 1",
        profile_version=NOW,
    )
    condition = result["sections"]["entry_triggers"]["conditions"][0]
    assert condition["status"] == "CONTRACT_REJECT"
    assert condition["reason_codes"] == ["FEATURE_TTL_EXPIRED"]


def test_closed_only_rejects_open_candle_and_current_allowed_does_not_relabel_it():
    asset = _asset()
    candidate = asset["_merged_indicators"].candidates[1]
    candidate["candle_closed"] = False
    closed = build_authorization_contract(
        asset=asset,
        profile_config=_single_rsi_profile(),
        legacy_decision="ALLOW",
        evaluated_at=NOW,
        profile_id="profile-1",
        profile_name="Profile 1",
        profile_version=NOW,
    )
    assert closed["sections"]["entry_triggers"]["conditions"][0]["reason_codes"] == [
        "CANDLE_OPEN_FORBIDDEN"
    ]

    candidate["candle_policy"] = "CURRENT_ALLOWED"
    current = build_authorization_contract(
        asset=asset,
        profile_config=_single_rsi_profile(candle_policy="CURRENT_ALLOWED"),
        legacy_decision="ALLOW",
        evaluated_at=NOW,
        profile_id="profile-1",
        profile_name="Profile 1",
        profile_version=NOW,
    )
    condition = current["sections"]["entry_triggers"]["conditions"][0]
    assert condition["status"] == "PASS"
    assert condition["resolved_feature"]["candle_closed"] is False
    assert condition["feature_identity"]["candle_policy"] == "CURRENT_ALLOWED"


def test_provider_policy_mismatch_is_not_silently_accepted():
    result = build_authorization_contract(
        asset=_asset(),
        profile_config=_single_rsi_profile(provider_policy_id="provider-policy-v2"),
        legacy_decision="ALLOW",
        evaluated_at=NOW,
        profile_id="profile-1",
        profile_name="Profile 1",
        profile_version=NOW,
    )
    condition = result["sections"]["entry_triggers"]["conditions"][0]
    assert condition["reason_codes"] == ["PROVIDER_POLICY_MISMATCH"]


def test_order_book_candidate_never_satisfies_trade_flow_condition():
    asset = _asset()
    asset.pop("_l3_live_order_flow_snapshot")
    asset["_l3_live_order_book_snapshot"] = {
        "values": {"orderbook_pressure": 0.8},
        "meta": {
            "source_provider": "gate_io_orderbook_ws",
            "provider_policy_id": "orderbook-policy-v1",
            "snapshot": True,
            "source_timestamp": "2026-08-25T04:44:40Z",
            "age_seconds": 4,
        },
    }
    profile = {
        "filters": {"conditions": []},
        "signals": {"conditions": []},
        "entry_triggers": {"conditions": [{
            "indicator": "orderbook_pressure",
            "source": "live_trade_flow",
            "source_provider": "gate_io_orderbook_ws",
            "provider_policy_id": "orderbook-policy-v1",
            "window_seconds": 300,
            "max_age_seconds": 30,
            "operator": ">",
            "value": 0,
            "required": True,
        }]},
        "block_rules": {"blocks": []},
    }
    result = build_authorization_contract(
        asset=asset,
        profile_config=profile,
        legacy_decision="ALLOW",
        evaluated_at=NOW,
        profile_id="profile-1",
        profile_name="Profile 1",
        profile_version=NOW,
    )
    condition = result["sections"]["entry_triggers"]["conditions"][0]
    assert condition["reason_codes"] == ["SOURCE_MISMATCH"]


def test_enabled_block_condition_is_required_by_default():
    profile = {
        "filters": {"conditions": []},
        "signals": {"conditions": []},
        "entry_triggers": {"conditions": []},
        "block_rules": {"blocks": [{
            "id": "missing-required-rsi",
            "logic": "AND",
            "conditions": [{
                "indicator": "rsi",
                "source": "ohlcv",
                "source_provider": "gate_io_candles",
                "provider_policy_id": "provider-policy-v1",
                "timeframe": "1h",
                "period": 14,
                "candle_policy": "CLOSED_ONLY",
                "max_age_seconds": 30,
                "operator": ">",
                "value": 90,
            }],
        }]},
    }
    result = build_authorization_contract(
        asset=_asset(),
        profile_config=profile,
        legacy_decision="ALLOW",
        evaluated_at=NOW,
        profile_id="profile-1",
        profile_name="Profile 1",
        profile_version=NOW,
    )
    condition = result["sections"]["block_rules"]["blocks"][0]["conditions"][0]
    assert condition["required"] is True
    assert condition["status"] == "CONTRACT_REJECT"
    assert condition["reason_codes"] == ["TIMEFRAME_MISMATCH"]


def test_ml_is_advisory_for_missing_and_unfavorable_models():
    missing = _ml_gate_audit_payload(
        {"score_status": "SKIPPED", "reason_code": "NO_ELIGIBLE_MODEL_FOR_LANE"},
        decision_after_ml="ALLOW",
    )
    assert missing["ml_status"] == "NOT_APPLIED"
    assert missing["ml_operational_effect"] is False
    assert "ML_GATE_ALLOWED" not in missing["reason_codes"]
    assert _ml_gate_should_block({"score_status": "OK", "model_approved": False}) is False

    l1_only = _ml_gate_audit_payload(
        {
            "score_status": "OK",
            "model_lane": "L1_SPECTRUM",
            "model_id": "l1-model",
            "win_fast_probability": 0.73,
            "reason_code": "NO_ELIGIBLE_MODEL_FOR_LANE",
        },
        decision_after_ml="ALLOW",
    )
    assert l1_only["ml_status"] == "NOT_APPLIED"
    assert l1_only["ml_reason_code"] == "NO_ELIGIBLE_MODEL_FOR_LANE"
    assert l1_only["ml_operational_effect"] is False


def test_aster_legacy_missing_block_section_is_unresolved_not_hash_divergence():
    overall = "701bd392dbf8b2d4e6434bbe1be7dd975d85ef5d4af4023650c8dc175c205813"
    legacy_gate = {
        "evaluation_envelope_hash": overall,
        "score": {"evaluation_envelope_hash": overall},
        "signals": {"evaluation_envelope_hash": overall},
        "entry_triggers": {"evaluation_envelope_hash": overall},
        "block_rules": None,
    }
    audit = _classify_legacy_section_hashes(legacy_gate)
    assert audit["status"] == "LEGACY_UNRESOLVED"
    assert audit["missing_sections"] == ["block_rules"]
    assert audit["mismatched_sections"] == []


def test_aster_profile_without_block_rules_section_contract_rejects():
    profile = {
        "default_timeframe": "5m",
        "filters": {"conditions": []},
        "signals": {"conditions": []},
        "entry_triggers": {"conditions": []},
    }
    result = build_authorization_contract(
        asset=_asset(symbol="ASTER_USDT"), profile_config=profile,
        legacy_decision="BLOCK", evaluated_at=NOW,
        profile_id="profile-aster", profile_name="ASTER", profile_version=NOW,
    )
    blocks = result["sections"]["block_rules"]
    assert blocks["contract_reject"] is True
    assert blocks["reason_codes"] == ["BLOCK_RULES_SECTION_MISSING"]
    assert result["authorization_status"] == "CONTRACT_REJECT"
