from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.l3_authorization_contract_v3 import (
    build_authorization_contract,
    build_feature_registry,
    canonical_hash,
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


def _resolver_policy(*, profile_id="profile-canary", enabled=True):
    return {
        "l3_v3_provenance_resolver": {
            "enabled": enabled,
            "profile_allowlist": [profile_id],
            "policy_version": "l3_v3_provenance_resolver_v1",
            "source_policies": {
                "ohlcv": {
                    "allowed_source_providers": ["gate_io_candles"],
                    "provider_policy_id": "ohlcv-policy-v1",
                    "max_age_seconds": 30,
                    "timeframe": "5m",
                    "candle_policy": "CLOSED_ONLY",
                },
                "live_trade_flow": {
                    "allowed_source_providers": ["gate_io_trades_ws"],
                    "provider_policy_id": "trade-flow-policy-v1",
                    "max_age_seconds": 30,
                    "window_seconds": 300,
                },
                "live_order_book": {
                    "allowed_source_providers": ["gate_io_orderbook_ws"],
                    "provider_policy_id": "order-book-policy-v1",
                    "max_age_seconds": 30,
                    "snapshot": True,
                },
                "decision_context": {
                    "allowed_source_providers": ["market_metadata", "robust_score"],
                    "provider_policy_id": "decision-context-policy-v1",
                    "max_age_seconds": 30,
                },
            },
        },
    }


def _legacy_profile(condition):
    return {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "signals": {"logic": "AND", "conditions": [condition]},
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
    }


def _gate_trace(condition_id, *, actual, target=None, status="PASS"):
    return {
        "filters": {"conditions": []},
        "signals": {"conditions": [{
            "condition_id": condition_id,
            "actual": actual,
            "target": target,
            "status": status,
        }]},
        "entry_triggers": {"conditions": []},
        "block_rules": {"evaluated": []},
    }


def _build_resolved(profile, asset, gate, *, legacy_decision="ALLOW"):
    return build_authorization_contract(
        asset=asset,
        profile_config=profile,
        legacy_decision=legacy_decision,
        evaluated_at=NOW,
        profile_id="profile-canary",
        profile_name="Canary",
        profile_version=NOW,
        gate_evaluation=gate,
        runtime_policy=_resolver_policy(),
    )


def test_resolver_materializes_legacy_threshold_without_mutating_profile_hash():
    condition = {
        "id": "legacy-rsi",
        "indicator": "rsi",
        "operator": ">",
        "value": 50,
        "period": 14,
        "required": True,
    }
    profile = _legacy_profile(condition)
    before = canonical_hash(profile)
    result = _build_resolved(
        profile,
        _asset(),
        _gate_trace("legacy-rsi", actual=58.7, target=50),
    )
    evaluated = result["sections"]["signals"]["conditions"][0]
    assert canonical_hash(profile) == before
    assert result["profile_lineage"]["profile_config_hash"] == before
    assert result["provenance_resolution"]["status"] == "RESOLVED"
    assert result["provenance_resolution"]["resolved_condition_count"] == 1
    assert evaluated["status"] == "PASS"
    assert evaluated["feature_identity"]["source"] == "ohlcv"
    assert evaluated["provider_policy_id"] == "ohlcv-policy-v1"
    assert result["authorization_status"] == "ALLOW"


def test_comparison_resolves_left_and_right_independently_without_false_indicator_error():
    asset = _asset()
    asset.update({"price": 100.0, "_price_source_at": NOW})
    condition = {
        "id": "price-over-rsi",
        "type": "comparison",
        "left": "price",
        "right": "rsi",
        "operator": ">",
        "required": True,
    }
    profile = _legacy_profile(condition)
    result = _build_resolved(
        profile,
        asset,
        _gate_trace("price-over-rsi", actual=100.0, target=58.7),
    )
    evaluated = result["sections"]["signals"]["conditions"][0]
    assert evaluated["status"] == "PASS"
    assert evaluated["actual"] == 100.0
    assert evaluated["target"] == 58.7
    assert list(evaluated["resolved_operands"]) == ["left", "right"]
    assert evaluated["feature_identities"]["left"]["source"] == "decision_context"
    assert evaluated["feature_identities"]["right"]["source"] == "ohlcv"
    assert "INDICATOR_REQUIRED" not in result["reason_codes"]
    assert result["authorization_status"] == "ALLOW"


def test_raw_comparison_contract_reports_operand_provenance_not_indicator_required():
    errors = validate_profile_contract({
        "signals": {"conditions": [{
            "type": "comparison",
            "left": "price",
            "right": "rsi",
            "operator": ">",
            "required": True,
        }]},
    })
    codes = [item["code"] for item in errors]
    assert codes == ["LEFT:SOURCE_REQUIRED", "RIGHT:SOURCE_REQUIRED"]
    assert "INDICATOR_REQUIRED" not in codes


def test_resolver_rejects_ambiguous_exact_feature_identity():
    asset = _asset()
    duplicate = dict(asset["_merged_indicators"].candidates[1])
    asset["_merged_indicators"].candidates.append(duplicate)
    condition = {
        "id": "ambiguous-rsi",
        "indicator": "rsi",
        "operator": ">",
        "value": 50,
        "required": True,
    }
    result = _build_resolved(
        _legacy_profile(condition),
        asset,
        _gate_trace("ambiguous-rsi", actual=58.7, target=50),
    )
    evaluated = result["sections"]["signals"]["conditions"][0]
    assert evaluated["status"] == "CONTRACT_REJECT"
    assert evaluated["reason_codes"] == [
        "FEATURE_PROVENANCE_AMBIGUOUS",
        "SOURCE_REQUIRED",
    ]
    assert result["provenance_resolution"]["status"] == "CONTRACT_REJECT"
    assert result["authorization_status"] == "CONTRACT_REJECT"


def test_resolver_rejects_unconfigured_source_policy_without_fallback():
    policy = _resolver_policy()
    policy["l3_v3_provenance_resolver"]["source_policies"].pop("ohlcv")
    condition = {
        "id": "unconfigured-rsi",
        "indicator": "rsi",
        "operator": ">",
        "value": 50,
        "required": True,
    }
    result = build_authorization_contract(
        asset=_asset(),
        profile_config=_legacy_profile(condition),
        legacy_decision="ALLOW",
        evaluated_at=NOW,
        profile_id="profile-canary",
        profile_name="Canary",
        profile_version=NOW,
        gate_evaluation=_gate_trace("unconfigured-rsi", actual=58.7, target=50),
        runtime_policy=policy,
    )
    assert result["provenance_resolution"]["errors"] == [{
        "path": "signals.conditions[0]",
        "code": "PROVENANCE_POLICY_UNCONFIGURED:ohlcv",
    }]
    assert result["authorization_status"] == "CONTRACT_REJECT"


def test_resolver_uses_frozen_score_context_with_real_decision_timestamp():
    asset = _asset()
    asset["_score"] = 72.5
    asset["_score_components"] = {
        "component_fields": {"momentum_score": 61.0},
    }
    condition = {
        "id": "score-threshold",
        "indicator": "score",
        "operator": ">=",
        "value": 70,
        "required": True,
    }
    result = _build_resolved(
        _legacy_profile(condition),
        asset,
        _gate_trace("score-threshold", actual=72.5, target=70),
    )
    evaluated = result["sections"]["signals"]["conditions"][0]
    assert evaluated["status"] == "PASS"
    assert evaluated["indicator"] == "alpha_score"
    assert evaluated["feature_identity"]["source"] == "decision_context"
    assert evaluated["resolved_feature"]["source_timestamp"] == "2026-08-25T04:44:44Z"


def test_resolver_materializes_trade_flow_and_order_book_without_cross_source_fallback():
    trade_condition = {
        "id": "live-taker",
        "indicator": "taker_ratio",
        "operator": ">",
        "value": 0.5,
        "required": True,
    }
    trade = _build_resolved(
        _legacy_profile(trade_condition),
        _asset(),
        _gate_trace("live-taker", actual=0.576241, target=0.5),
    )
    trade_eval = trade["sections"]["signals"]["conditions"][0]
    assert trade_eval["status"] == "PASS"
    assert trade_eval["feature_identity"]["source"] == "live_trade_flow"

    order_asset = _asset()
    order_asset["_l3_live_order_book_snapshot"] = {
        "values": {"orderbook_pressure": 0.8},
        "meta": {
            "source_provider": "gate_io_orderbook_ws",
            "snapshot": True,
            "source_timestamp": "2026-08-25T04:44:40Z",
            "age_seconds": 4,
        },
    }
    order_condition = {
        "id": "book-pressure",
        "indicator": "orderbook_pressure",
        "operator": ">",
        "value": 0.5,
        "required": True,
    }
    order = _build_resolved(
        _legacy_profile(order_condition),
        order_asset,
        _gate_trace("book-pressure", actual=0.8, target=0.5),
    )
    order_eval = order["sections"]["signals"]["conditions"][0]
    assert order_eval["status"] == "PASS"
    assert order_eval["feature_identity"]["source"] == "live_order_book"


def test_current_allowed_policy_does_not_require_candle_closed_state():
    asset = _asset()
    candidate = asset["_merged_indicators"].candidates[1]
    candidate.pop("candle_closed", None)
    candidate.pop("candle_policy", None)
    policy = _resolver_policy()
    policy["l3_v3_provenance_resolver"]["source_policies"]["ohlcv"][
        "candle_policy"
    ] = "CURRENT_ALLOWED"
    condition = {
        "id": "current-rsi",
        "indicator": "rsi",
        "operator": ">",
        "value": 50,
        "required": True,
    }
    result = build_authorization_contract(
        asset=asset,
        profile_config=_legacy_profile(condition),
        legacy_decision="ALLOW",
        evaluated_at=NOW,
        profile_id="profile-canary",
        profile_name="Canary",
        profile_version=NOW,
        gate_evaluation=_gate_trace("current-rsi", actual=58.7, target=50),
        runtime_policy=policy,
    )
    evaluated = result["sections"]["signals"]["conditions"][0]
    assert evaluated["status"] == "PASS"
    assert evaluated["feature_identity"]["candle_policy"] == "CURRENT_ALLOWED"


def test_resolved_feature_without_timestamp_is_fail_closed():
    asset = _asset()
    asset.update({"price": 100.0, "_price_source_at": None})
    condition = {
        "id": "price-present-but-undated",
        "indicator": "price",
        "operator": ">",
        "value": 50,
        "required": True,
    }
    result = _build_resolved(
        _legacy_profile(condition),
        asset,
        _gate_trace("price-present-but-undated", actual=100.0, target=50),
    )
    evaluated = result["sections"]["signals"]["conditions"][0]
    assert evaluated["status"] == "CONTRACT_REJECT"
    assert evaluated["reason_codes"] == [
        "SOURCE_TIMESTAMP_MISSING",
        "FEATURE_AGE_UNKNOWN",
    ]
    assert result["authorization_status"] == "CONTRACT_REJECT"


def test_global_entry_trigger_is_resolved_as_an_additional_and_gate():
    asset = _asset()
    profile = _legacy_profile({
        "id": "signal-rsi",
        "indicator": "rsi",
        "operator": ">",
        "value": 50,
        "required": True,
    })
    profile["_global_entry_triggers"] = {
        "logic": "AND",
        "conditions": [{
            "id": "global-taker",
            "indicator": "taker_ratio",
            "operator": ">",
            "value": 0.5,
            "required": True,
        }],
    }
    gate = _gate_trace("signal-rsi", actual=58.7, target=50)
    gate["global_entry_triggers"] = {"conditions": [{
        "condition_id": "global-taker",
        "actual": 0.576241,
        "target": 0.5,
        "status": "PASS",
    }]}
    result = _build_resolved(profile, asset, gate)
    global_gate = result["sections"]["global_entry_triggers"]
    assert global_gate["passed"] is True
    assert global_gate["conditions"][0]["status"] == "PASS"
    assert global_gate["conditions"][0]["feature_identity"]["source"] == (
        "live_trade_flow"
    )
    assert result["authorization_status"] == "ALLOW"


def test_persisted_profile_hash_excludes_runtime_metadata_and_keeps_execution_contract():
    condition = {
        "id": "legacy-rsi",
        "indicator": "rsi",
        "operator": ">",
        "value": 50,
        "period": 14,
        "required": True,
    }
    persisted = _legacy_profile(condition)
    persisted_hash = canonical_hash(persisted)
    runtime = {
        **persisted,
        "_l3_gate_runtime_policy": _resolver_policy(),
        "_block_rules_lineage": {"effective_block_rules_hash": "runtime-only"},
        "_execution_contract": {
            "contract_valid": True,
            "status": "MATCH",
            "profile_id": "profile-canary",
            "profile_version_id": "version-immutable-1",
            "profile_projection_hash": persisted_hash,
            "profile_projection": persisted,
        },
    }
    result = _build_resolved(
        runtime,
        _asset(),
        _gate_trace("legacy-rsi", actual=58.7, target=50),
    )
    assert result["profile_lineage"]["profile_config_hash"] == persisted_hash
    assert result["profile_lineage"]["rules_snapshot"] == persisted
    assert result["profile_lineage"]["profile_version_id"] == (
        "version-immutable-1"
    )
    assert result["profile_execution_contract"]["status"] == "MATCH"
    assert result["authorization_status"] == "ALLOW"


def test_multiple_conditions_can_resolve_the_same_evidence_without_registry_mutation():
    profile = {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "signals": {"logic": "AND", "conditions": [
            {
                "id": "rsi-floor",
                "indicator": "rsi",
                "operator": ">",
                "value": 50,
                "period": 14,
                "required": True,
            },
            {
                "id": "rsi-ceiling",
                "indicator": "rsi",
                "operator": "<",
                "value": 70,
                "period": 14,
                "required": True,
            },
        ]},
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
    }
    gate = _gate_trace("rsi-floor", actual=58.7, target=50)
    gate["signals"]["conditions"].append({
        "condition_id": "rsi-ceiling",
        "actual": 58.7,
        "target": 70,
        "status": "PASS",
    })
    result = _build_resolved(profile, _asset(), gate)
    conditions = result["sections"]["signals"]["conditions"]
    assert [condition["status"] for condition in conditions] == ["PASS", "PASS"]
    assert conditions[0]["resolved_feature_hash"] == conditions[1][
        "resolved_feature_hash"
    ]


def test_block_rule_trace_resolves_by_rule_and_condition_identity():
    profile = {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "signals": {"logic": "AND", "conditions": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": [{
            "id": "rsi-exhaustion",
            "logic": "AND",
            "conditions": [{
                "indicator": "rsi",
                "operator": ">",
                "value": 90,
                "period": 14,
            }],
        }]},
    }
    gate = {
        "filters": {"conditions": []},
        "signals": {"conditions": []},
        "entry_triggers": {"conditions": []},
        "block_rules": {"evaluated": [{
            "id": "rsi-exhaustion",
            "conditions": [{
                "indicator": "rsi",
                "operator": ">",
                "expected": 90,
                "actual": 58.7,
                "status": "FAIL",
            }],
        }]},
    }
    result = _build_resolved(profile, _asset(), gate)
    block = result["sections"]["block_rules"]["blocks"][0]
    assert block["status"] == "NOT_MATCHED"
    assert block["conditions"][0]["status"] == "FAIL"
    assert block["conditions"][0]["actual"] == 58.7
    assert result["authorization_status"] == "ALLOW"


def test_configured_period_must_be_present_in_producer_evidence():
    asset = _asset()
    asset["_merged_indicators"].candidates[1]["period"] = None
    condition = {
        "id": "rsi-period",
        "indicator": "rsi",
        "operator": ">",
        "value": 50,
        "period": 14,
        "required": True,
    }
    result = _build_resolved(
        _legacy_profile(condition),
        asset,
        _gate_trace("rsi-period", actual=58.7, target=50),
    )
    assert result["provenance_resolution"]["errors"] == [{
        "path": "signals.conditions[0]",
        "code": "FEATURE_IDENTITY_NOT_AVAILABLE",
    }]
    assert result["authorization_status"] == "CONTRACT_REJECT"
