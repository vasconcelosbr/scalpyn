from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.layer_context import CandleIdentity, ProfileIdentity
from app.services.mtf_observation_service import (
    _seal,
    advance_l2_setup_state,
    build_l1_context,
    build_l2_context,
    build_l3_confirmation,
    build_controlled_v5_replay,
    build_multilayer_context,
    verify_context_hash,
)
from app.services.indicators_provider import (
    build_grouped_indicators_snapshot,
    build_indicators_snapshot,
)
from app.utils.indicator_merge import MergedIndicators
from app.services.multilayer_contract import require_shadow_multilayer_config
from app.services.profile_engine import ProfileEngine
from app.services.profile_runtime_config import canonical_hash
from app.tasks.pipeline_scan import _apply_level_filter
from app.services.strategy_settings_service import (
    StrategySettingsService,
    StrategySettingsValidationError,
)
from app.services.mtf_walk_forward import (
    FoldResult,
    MTFCalibrationConfigRequired,
    chronological_folds,
    require_calibration_config,
    select_candidate,
)


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
IDENTITY = ProfileIdentity(
    profile_id="profile",
    profile_version_id="version",
    profile_config_hash="a" * 64,
)


def _candle(timeframe: str) -> CandleIdentity:
    return CandleIdentity(
        symbol="BTC_USDT",
        market_type="spot",
        timeframe=timeframe,
        source_timestamp=NOW - timedelta(minutes=15),
        closed=True,
        source_provider="gate.io",
        provider_policy_id="spot_gate_closed_ohlcv_v1",
    )


def test_strict_profile_uses_requested_timeframe_without_flat_fallback():
    profile = {
        "default_timeframe": "1h",
        "filters": {
            "logic": "AND",
            "conditions": [{"field": "rsi", "operator": ">", "value": 50}],
        },
    }
    asset = {
        "symbol": "BTC_USDT",
        "indicators": {"rsi": 80},
        "_indicators_by_tf": {"1h": {"rsi": 20}},
    }
    assert ProfileEngine(profile).evaluate_asset(asset)["passed_filter"] is True
    assert (
        ProfileEngine(profile, strict_timeframe_mode=True)
        .evaluate_asset(asset)["passed_filter"]
        is False
    )


def test_context_chain_hashes_and_wait_semantics():
    l1_profile = {
        "default_timeframe": "1h",
        "mtf_semantics": {
            "adx_strong_min": 25,
            "atr_pct_low": 0.4,
            "atr_pct_high": 1.2,
        },
    }
    l1_values = {
        "adx": 30,
        "atr_pct": 0.8,
        "di_plus": 32,
        "di_minus": 12,
        "ema21": 102,
        "ema50": 100,
        "ema21_slope_pct": 0.2,
        "ema50_slope_pct": 0.1,
        "higher_highs_5": True,
        "higher_lows_5": True,
    }
    l1 = build_l1_context(
        symbol="BTC_USDT",
        profile=l1_profile,
        profile_identity=IDENTITY,
        values=l1_values,
        candle=_candle("1h"),
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    l2_profile = {
        "default_timeframe": "15m",
        "mtf_semantics": {
            "max_extension_atr": 2.0,
            "pullback_max_distance_atr": 1.0,
            "breakout_min_distance_atr": 0.2,
            "retest_tolerance_atr": 0.3,
            "invalidation_atr": 0.5,
            "setup_valid_candles": 3,
            "adx_impulse_min": 20.0,
            "volume_relative_min": 1.2,
            "bb_width_compression_max": 0.02,
            "bb_width_expansion_min": 0.03,
        },
    }
    l2_values = {
        "price": 99.5,
        "atr": 2,
        "ema21": 100,
        "ema50": 99,
        "vwap": 100,
        "vwap_reclaim_bool": False,
        "bb_upper": 103,
        "bb_lower": 97,
        "di_plus": 30,
        "di_minus": 10,
        "higher_highs_5": True,
        "higher_lows_5": True,
        "adx": 28,
        "volume_spike": 1.5,
        "bb_width": 0.04,
    }
    first_transition = advance_l2_setup_state(
        values=l2_values,
        candle_open_at=NOW - timedelta(minutes=30),
        semantics=l2_profile["mtf_semantics"],
        previous=None,
    )
    l2_values = {**l2_values, "price": 101, "vwap_reclaim_bool": True}
    transition = advance_l2_setup_state(
        values=l2_values,
        candle_open_at=NOW - timedelta(minutes=15),
        semantics=l2_profile["mtf_semantics"],
        previous={
            **first_transition,
            "last_candle_open_at": first_transition["last_candle_open_at"],
        },
    )
    l2 = build_l2_context(
        symbol="BTC_USDT",
        profile=l2_profile,
        profile_identity=IDENTITY,
        values=l2_values,
        candle=_candle("15m"),
        expires_at=NOW + timedelta(minutes=5),
        l1_context=l1,
        now=NOW,
        state_transition=transition,
    )
    aggregate = build_multilayer_context(
        l1=l1,
        l2=l2,
        l3_confirmation=build_l3_confirmation(
            legacy_decision="ALLOW",
            indicators_snapshot={},
            gate_evaluation_hash="c" * 64,
            layer_config={
                "validity_margin_seconds": 30,
                "source_policies": {"ohlcv": {
                    "allowed_source_providers": ["gate.io"],
                    "provider_policy_id": "spot_gate_closed_ohlcv_v1",
                    "allowed_capture_contract_versions": ["gate_ohlcv_canonical_v1"],
                }},
            },
            now=NOW,
        ),
        canonical_score=70,
        calibration_run_id="run-id",
        now=NOW,
    )
    assert aggregate["operational_effect"] is False
    assert aggregate["observational_decision"] == "WAIT"
    verify_context_hash(aggregate)


def test_tampered_and_replayed_contexts_are_rejected():
    payload = {"contract_version": "x", "value": 1}
    from app.services.profile_runtime_config import canonical_hash

    payload["context_hash"] = canonical_hash({"contract_version": "x", "value": 1})
    payload["value"] = 2
    with pytest.raises(ValueError, match="HASH_INVALID"):
        verify_context_hash(payload)


def test_shadow_contract_requires_exact_layers_closed_only_and_no_authority():
    layers = {}
    for layer, timeframe in {"L1": "1h", "L2": "15m", "L3": "5m"}.items():
        layers[layer] = {
            "observational_enabled": True,
            "profile_id": layer if layer != "L3" else None,
            "profile_version_id": layer + "v" if layer != "L3" else None,
            "profile_config_hash": "b" * 64 if layer != "L3" else None,
            "default_timeframe": timeframe,
            "validity_margin_seconds": 30,
            "source_policies": {"ohlcv": {
                "allowed_source_providers": ["gate.io"],
                "provider_policy_id": "spot_gate_closed_ohlcv_v1",
                "candle_policy": "CLOSED_ONLY",
                "allowed_capture_contract_versions": ["gate_ohlcv_canonical_v1"],
            }},
            "required_indicators_by_group": {"structural": ["adx"]},
        }
    config = {
        "multilayer_contract": {
            "enabled": True,
            "activation_mode": "SHADOW",
            "operational_effect": False,
            "decision_feature_contract_version": "multilayer_decision_context_v3",
            "calibration_run_id": "run-id",
            "layers": layers,
        }
    }
    assert require_shadow_multilayer_config(config)["operational_effect"] is False
    config["multilayer_contract"]["operational_effect"] = True
    with pytest.raises(ValueError, match="FORBIDDEN"):
        require_shadow_multilayer_config(config)


def test_waived_shadow_contract_and_context_preserve_signed_disclosure():
    gate_material = {
        "status": "WAIVED_FOR_SHADOW",
        "run_status": "DRAFT_INSUFFICIENT_DATA",
        "failure_reason": "MIN_SAMPLES_NOT_MET",
        "calibration_run_id": "run-id",
        "policy_hash": "c" * 64,
        "dataset_hash": "d" * 64,
        "authorization_scope": "OBSERVATIONAL_ONLY",
        "calibration_not_passed_acknowledged": True,
        "thresholds_unvalidated_acknowledged": True,
        "operational_effect_false_acknowledged": True,
        "authorized_by": "user-id",
        "authorized_at": NOW.isoformat(),
    }
    gate = {**gate_material, "authorization_hash": canonical_hash(gate_material)}
    layers = {}
    for layer, timeframe in {"L1": "1h", "L2": "15m", "L3": "5m"}.items():
        layers[layer] = {
            "observational_enabled": True,
            "profile_id": layer if layer != "L3" else None,
            "profile_version_id": layer + "v" if layer != "L3" else None,
            "profile_config_hash": "b" * 64 if layer != "L3" else None,
            "default_timeframe": timeframe,
            "validity_margin_seconds": 30,
            "source_policies": {"ohlcv": {
                "allowed_source_providers": ["gate.io"],
                "provider_policy_id": "spot_gate_closed_ohlcv_v1",
                "candle_policy": "CLOSED_ONLY",
                "allowed_capture_contract_versions": ["gate_ohlcv_canonical_v1"],
            }},
            "required_indicators_by_group": {"structural": ["adx"]},
        }
    config = {"multilayer_contract": {
        "enabled": True,
        "activation_mode": "SHADOW",
        "operational_effect": False,
        "decision_feature_contract_version": "multilayer_decision_context_v4",
        "calibration_run_id": "run-id",
        "statistical_gate": gate,
        "layers": layers,
    }}

    assert require_shadow_multilayer_config(config)["statistical_gate"] == gate
    tampered = {**config, "multilayer_contract": {
        **config["multilayer_contract"],
        "statistical_gate": {**gate, "dataset_hash": "e" * 64},
    }}
    with pytest.raises(ValueError, match="WAIVER_HASH_INVALID"):
        require_shadow_multilayer_config(tampered)


def test_v5_contract_requires_complete_hashed_validity_evidence():
    gate_material = {
        "status": "WAIVED_FOR_SHADOW", "run_status": "DRAFT_INSUFFICIENT_DATA",
        "failure_reason": "MIN_SAMPLES_NOT_MET", "calibration_run_id": "run-id",
        "policy_hash": "c" * 64, "dataset_hash": "d" * 64,
        "authorization_scope": "OBSERVATIONAL_ONLY",
        "calibration_not_passed_acknowledged": True,
        "thresholds_unvalidated_acknowledged": True,
        "operational_effect_false_acknowledged": True,
        "authorized_by": "user-id", "authorized_at": NOW.isoformat(),
    }
    gate = {**gate_material, "authorization_hash": canonical_hash(gate_material)}

    def evidence(margin: int):
        material = {
            "formula": "p99(open_to_available_seconds)+scan_interval_seconds",
            "window_started_at": (NOW - timedelta(hours=24)).isoformat(),
            "window_ended_at": NOW.isoformat(), "sample_count": 65,
            "active_symbol_count": 65, "covered_symbol_count": 65,
            "p99_open_to_available_seconds": float(margin - 30),
            "scan_interval_seconds": 30, "validity_margin_seconds": margin,
            "measured_at": NOW.isoformat(),
        }
        return {**material, "evidence_hash": canonical_hash(material)}

    layers = {}
    for layer, timeframe in {"L1": "1h", "L2": "15m", "L3": "5m"}.items():
        margin = 90
        layers[layer] = {
            "observational_enabled": True,
            "profile_id": layer if layer != "L3" else None,
            "profile_version_id": layer + "v" if layer != "L3" else None,
            "profile_config_hash": "b" * 64 if layer != "L3" else None,
            "default_timeframe": timeframe, "validity_margin_seconds": margin,
            "source_policies": {"ohlcv": {
                "allowed_source_providers": ["gate.io"],
                "provider_policy_id": "policy", "candle_policy": "CLOSED_ONLY",
                "allowed_capture_contract_versions": ["capture"],
            }},
            "required_indicators_by_group": {"structural": ["adx"]},
        }
        if layer == "L3":
            layers[layer]["validity_margin_seconds_by_group"] = {"structural": margin}
            layers[layer]["validity_evidence_by_group"] = {"structural": evidence(margin)}
        else:
            layers[layer]["validity_evidence"] = evidence(margin)
    config = {"multilayer_contract": {
        "enabled": True, "activation_mode": "SHADOW", "operational_effect": False,
        "provenance_policy_version": "multilayer_provenance_resolver_v2",
        "decision_feature_contract_version": "multilayer_decision_context_v5",
        "calibration_run_id": "run-id", "statistical_gate": gate, "layers": layers,
    }}
    assert require_shadow_multilayer_config(config)["layers"]["L1"]["validity_margin_seconds"] == 90
    config["multilayer_contract"]["layers"]["L1"]["validity_evidence"]["sample_count"] = 64
    with pytest.raises(ValueError, match="VALIDITY_EVIDENCE_HASH_INVALID"):
        require_shadow_multilayer_config(config)


def test_l3_confirmation_validates_declared_inputs_not_auxiliary_snapshot_fields():
    source_at = NOW - timedelta(minutes=5)
    envelope = {
        "value": 101.0,
        "timeframe": "5m",
        "market_type": "spot",
        "scheduler_group": "microstructure",
        "source_provider": "gate.io",
        "provider_policy_id": "spot_gate_closed_ohlcv_v1",
        "candle_policy": "CLOSED_ONLY",
        "candle_closed": True,
        "source_timestamp": source_at.isoformat(),
        "available_at": NOW.isoformat(),
        "config_hash": "c" * 64,
        "config_profile_id": "config-id",
        "producer_version": "compute_5m_v2",
        "capture_contract_version": "gate_ohlcv_canonical_v1",
    }
    envelope["envelope_hash"] = canonical_hash(envelope)
    snapshot = {
        "price": {
            "value": 101.0,
            "source_group": "microstructure",
            "ts": NOW.isoformat(),
            "timeframe": "5m",
            "observed_timeframes": ["5m"],
            "timeframe_conflict": False,
            "stale": False,
            "source_timestamp": source_at.isoformat(),
            "available_at": NOW.isoformat(),
            "source_provider": "gate.io",
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "candle_closed": True,
            "config_hash": "c" * 64,
            "config_profile_id": "config-id",
            "producer_version": "compute_5m_v2",
            "envelope": envelope,
        },
        "score": {"value": 88, "source_group": "decision_context"},
    }
    layer = {
        "validity_margin_seconds": 60,
        "required_indicators_by_group": {"microstructure": ["price"]},
        "source_policies": {"ohlcv": {
            "allowed_source_providers": ["gate.io"],
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "allowed_capture_contract_versions": ["gate_ohlcv_canonical_v1"],
            "allowed_producer_versions": ["compute_5m_v2"],
            "indicator_config_profile_id": "config-id",
            "indicator_config_hash": "c" * 64,
        }},
    }

    confirmation = build_l3_confirmation(
        legacy_decision="ALLOW",
        indicators_snapshot=snapshot,
        gate_evaluation_hash="f" * 64,
        layer_config=layer,
        now=NOW,
    )

    assert confirmation["verdict"] == "PASS"
    assert confirmation["invalid_indicators"] == []


def test_grouped_mtf_snapshot_resolves_homonymous_indicator_without_changing_legacy():
    merged = MergedIndicators()
    merged.values["price"] = 111.0
    merged.meta["price"] = {
        "group": "structural", "timeframe": "5m", "timestamp": NOW,
    }
    for group, value, source_at in (
        ("structural", 111.0, NOW),
        ("microstructure", 222.0, NOW - timedelta(seconds=1)),
    ):
        envelope = {
            "value": value, "timeframe": "5m", "market_type": "spot",
            "scheduler_group": group, "source_timestamp": source_at.isoformat(),
            "available_at": NOW.isoformat(), "computed_at": NOW.isoformat(),
            "source_provider": "gate.io", "provider_policy_id": "policy",
            "candle_policy": "CLOSED_ONLY", "candle_closed": True,
            "config_profile_id": "config-id", "config_hash": "c" * 64,
            "producer_version": "producer", "capture_contract_version": "capture",
        }
        envelope["envelope_hash"] = canonical_hash(envelope)
        merged.candidates.append({
            "indicator": "price", "actual": value, "group": group,
            "timeframe": "5m", "market_type": "spot", "stale": False,
            "source_timestamp": source_at, "available_at": NOW,
            "computed_at": NOW, "source_provider": "gate.io",
            "provider_policy_id": "policy", "candle_closed": True,
            "config_profile_id": "config-id", "config_hash": "c" * 64,
            "producer_version": "producer", "envelope": envelope,
        })

    legacy = build_indicators_snapshot(merged, keys=["price"])
    grouped = build_grouped_indicators_snapshot(
        merged,
        required_by_group={"structural": ["price"], "microstructure": ["price"]},
        timeframe="5m",
    )

    assert legacy["price"]["value"] == 111.0
    assert legacy["price"]["source_group"] == "structural"
    assert grouped["structural"]["price"]["value"] == 111.0
    assert grouped["microstructure"]["price"]["value"] == 222.0


def test_controlled_v5_replay_can_pass_without_operational_effect():
    source_at = NOW - timedelta(minutes=5)
    envelope = {
        "value": 101.0, "timeframe": "5m", "market_type": "spot",
        "scheduler_group": "microstructure", "source_provider": "gate.io",
        "provider_policy_id": "policy", "candle_policy": "CLOSED_ONLY",
        "candle_closed": True, "source_timestamp": source_at.isoformat(),
        "available_at": NOW.isoformat(), "config_profile_id": "config-id",
        "config_hash": "c" * 64, "producer_version": "producer",
        "capture_contract_version": "capture",
    }
    envelope["envelope_hash"] = canonical_hash(envelope)
    grouped = {"microstructure": {"price": {
        "value": 101.0, "source_group": "microstructure", "ts": NOW.isoformat(),
        "timeframe": "5m", "observed_timeframes": ["5m"],
        "timeframe_conflict": False, "stale": False,
        "source_timestamp": source_at.isoformat(), "available_at": NOW.isoformat(),
        "source_provider": "gate.io", "provider_policy_id": "policy",
        "candle_closed": True, "config_profile_id": "config-id",
        "config_hash": "c" * 64, "producer_version": "producer",
        "fallback_used": False, "envelope": envelope,
    }}}
    l3 = build_l3_confirmation(
        legacy_decision="ALLOW", indicators_snapshot={},
        grouped_indicators_snapshot=grouped,
        gate_evaluation_hash="f" * 64,
        layer_config={
            "validity_margin_seconds": 60,
            "required_indicators_by_group": {"microstructure": ["price"]},
            "source_policies": {"ohlcv": {
                "allowed_source_providers": ["gate.io"],
                "provider_policy_id": "policy",
                "allowed_capture_contract_versions": ["capture"],
                "allowed_producer_versions": ["producer"],
                "indicator_config_profile_id": "config-id",
                "indicator_config_hash": "c" * 64,
            }},
        }, now=NOW,
    )
    l1 = _seal({"contract_version": "l1_decision_context_v3", "verdict": "PASS"})
    l2 = _seal({
        "contract_version": "l2_decision_context_v3", "verdict": "PASS",
        "l1_context_hash": l1["context_hash"],
    })
    gate_material = {
        "status": "WAIVED_FOR_SHADOW", "run_status": "DRAFT_INSUFFICIENT_DATA",
        "failure_reason": "MIN_SAMPLES_NOT_MET", "calibration_run_id": "run-id",
        "policy_hash": "a" * 64, "dataset_hash": "b" * 64,
        "authorization_scope": "OBSERVATIONAL_ONLY",
        "calibration_not_passed_acknowledged": True,
        "thresholds_unvalidated_acknowledged": True,
        "operational_effect_false_acknowledged": True,
        "authorized_by": "user", "authorized_at": NOW.isoformat(),
    }
    aggregate = build_multilayer_context(
        l1=l1, l2=l2, l3_confirmation=l3, canonical_score=90,
        calibration_run_id="run-id", now=NOW,
        statistical_gate={
            **gate_material, "authorization_hash": canonical_hash(gate_material),
        },
    )
    assert aggregate["contract_version"] == "multilayer_decision_context_v5"
    assert aggregate["observational_decision"] == "PASS"
    assert aggregate["operational_effect"] is False


def test_contract_driven_controlled_replay_is_synthetic_and_non_operational():
    gate_material = {
        "status": "WAIVED_FOR_SHADOW",
        "run_status": "DRAFT_INSUFFICIENT_DATA",
        "failure_reason": "MIN_SAMPLES_NOT_MET",
        "calibration_run_id": "run-id",
        "policy_hash": "a" * 64,
        "dataset_hash": "b" * 64,
        "authorization_scope": "OBSERVATIONAL_ONLY",
        "calibration_not_passed_acknowledged": True,
        "thresholds_unvalidated_acknowledged": True,
        "operational_effect_false_acknowledged": True,
        "authorized_by": "user",
        "authorized_at": NOW.isoformat(),
    }
    contract = {
        "decision_feature_contract_version": "multilayer_decision_context_v5",
        "provenance_policy_version": "multilayer_provenance_resolver_v2",
        "operational_effect": False,
        "calibration_run_id": "run-id",
        "statistical_gate": {
            **gate_material,
            "authorization_hash": canonical_hash(gate_material),
        },
        "layers": {"L3": {
            "validity_margin_seconds": 60,
            "validity_margin_seconds_by_group": {"microstructure": 60},
            "required_indicators_by_group": {"microstructure": ["price"]},
            "source_policies": {"ohlcv": {
                "allowed_source_providers": ["gate.io"],
                "provider_policy_id": "policy",
                "allowed_capture_contract_versions": ["capture"],
                "allowed_producer_versions": ["producer"],
                "indicator_config_profile_id": "config-id",
                "indicator_config_hash": "c" * 64,
            }},
        }},
    }

    replay = build_controlled_v5_replay(contract=contract, now=NOW)

    assert replay["synthetic_controlled_replay"] is True
    assert replay["status"] == "PASS"
    assert replay["layer_verdicts"] == {"L1": "PASS", "L2": "PASS", "L3": "PASS"}
    assert replay["hashes_valid"] is True
    assert replay["operational_effect"] is False

    with pytest.raises(ValueError, match="OPERATIONAL_EFFECT_FORBIDDEN"):
        build_controlled_v5_replay(
            contract={**contract, "operational_effect": True}, now=NOW
        )


def test_explicit_empty_grouped_snapshot_never_falls_back_to_legacy_flat_snapshot():
    source_at = NOW - timedelta(minutes=5)
    envelope = {
        "value": 101.0, "timeframe": "5m", "market_type": "spot",
        "scheduler_group": "microstructure", "source_provider": "gate.io",
        "provider_policy_id": "policy", "candle_policy": "CLOSED_ONLY",
        "candle_closed": True, "source_timestamp": source_at.isoformat(),
        "available_at": NOW.isoformat(), "config_profile_id": "config-id",
        "config_hash": "c" * 64, "producer_version": "producer",
        "capture_contract_version": "capture",
    }
    envelope["envelope_hash"] = canonical_hash(envelope)
    legacy = {"price": {
        "value": 101.0, "source_group": "microstructure", "ts": NOW.isoformat(),
        "timeframe": "5m", "observed_timeframes": ["5m"],
        "timeframe_conflict": False, "stale": False,
        "source_timestamp": source_at.isoformat(), "available_at": NOW.isoformat(),
        "source_provider": "gate.io", "provider_policy_id": "policy",
        "candle_closed": True, "config_profile_id": "config-id",
        "config_hash": "c" * 64, "producer_version": "producer",
        "fallback_used": False, "envelope": envelope,
    }}
    confirmation = build_l3_confirmation(
        legacy_decision="ALLOW", indicators_snapshot=legacy,
        grouped_indicators_snapshot={}, gate_evaluation_hash="f" * 64,
        layer_config={
            "validity_margin_seconds": 60,
            "required_indicators_by_group": {"microstructure": ["price"]},
            "source_policies": {"ohlcv": {
                "allowed_source_providers": ["gate.io"],
                "provider_policy_id": "policy",
                "allowed_capture_contract_versions": ["capture"],
                "allowed_producer_versions": ["producer"],
                "indicator_config_profile_id": "config-id",
                "indicator_config_hash": "c" * 64,
            }},
        }, now=NOW,
    )

    assert confirmation["verdict"] == "UNAVAILABLE"
    assert confirmation["reason_codes"] == ["L3_TEMPORAL_IDENTITY_UNAVAILABLE"]
    assert confirmation["invalid_indicators"] == ["price"]


def test_l3_rejects_envelope_metadata_that_disagrees_with_selected_candidate():
    source_at = NOW - timedelta(minutes=5)
    envelope = {
        "value": 101.0, "timeframe": "5m", "market_type": "spot",
        "scheduler_group": "microstructure", "source_provider": "wrong-provider",
        "provider_policy_id": "policy", "candle_policy": "CLOSED_ONLY",
        "candle_closed": True, "source_timestamp": source_at.isoformat(),
        "available_at": NOW.isoformat(), "config_profile_id": "config-id",
        "config_hash": "c" * 64, "producer_version": "producer",
        "capture_contract_version": "capture",
    }
    envelope["envelope_hash"] = canonical_hash(envelope)
    grouped = {"microstructure": {"price": {
        "value": 101.0, "source_group": "microstructure", "ts": NOW.isoformat(),
        "timeframe": "5m", "observed_timeframes": ["5m"],
        "timeframe_conflict": False, "stale": False,
        "source_timestamp": source_at.isoformat(), "available_at": NOW.isoformat(),
        "source_provider": "gate.io", "provider_policy_id": "policy",
        "candle_closed": True, "config_profile_id": "config-id",
        "config_hash": "c" * 64, "producer_version": "producer",
        "fallback_used": False, "envelope": envelope,
    }}}
    confirmation = build_l3_confirmation(
        legacy_decision="ALLOW", indicators_snapshot={},
        grouped_indicators_snapshot=grouped, gate_evaluation_hash="f" * 64,
        layer_config={
            "validity_margin_seconds": 60,
            "required_indicators_by_group": {"microstructure": ["price"]},
            "source_policies": {"ohlcv": {
                "allowed_source_providers": ["gate.io"],
                "provider_policy_id": "policy",
                "allowed_capture_contract_versions": ["capture"],
                "allowed_producer_versions": ["producer"],
                "indicator_config_profile_id": "config-id",
                "indicator_config_hash": "c" * 64,
            }},
        }, now=NOW,
    )

    assert confirmation["verdict"] == "UNAVAILABLE"
    assert confirmation["invalid_indicators"] == ["price"]


def _coverage_payload(*, source_timestamp: datetime, envelope_hash: str | None = None):
    envelope = {
        "value": 42,
        "status": "available",
        "timeframe": "15m",
        "market_type": "spot",
        "scheduler_group": "structural",
        "source_provider": "gate.io",
        "provider_policy_id": "spot_gate_closed_ohlcv_v1",
        "candle_policy": "CLOSED_ONLY",
        "candle_closed": True,
        "source_timestamp": source_timestamp.isoformat(),
        "available_at": NOW.isoformat(),
        "config_hash": "c" * 64,
        "config_profile_id": "99999999-9999-9999-9999-999999999999",
        "producer_version": "mtf_indicator_producer_v1",
        "capture_contract_version": "gate_ohlcv_canonical_v1",
    }
    envelope["envelope_hash"] = envelope_hash or canonical_hash(envelope)
    return {"adx": envelope}


def test_activation_coverage_recomputes_hash_and_derives_expiry_from_contract():
    layer = {
        "validity_margin_seconds": 60,
        "source_policies": {"ohlcv": {
            "allowed_source_providers": ["gate.io"],
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "scheduler_group": "structural",
            "allowed_producer_versions": ["mtf_indicator_producer_v1"],
            "indicator_config_profile_id": "99999999-9999-9999-9999-999999999999",
            "indicator_config_hash": "c" * 64,
            "allowed_capture_contract_versions": ["gate_ohlcv_canonical_v1"],
        }},
        "required_indicators_by_group": {"structural": ["adx"]},
    }
    evidence = StrategySettingsService._assert_coverage_envelope(
        _coverage_payload(source_timestamp=NOW - timedelta(minutes=15)),
        symbol="BTC_USDT",
        timeframe="15m",
        scheduler_group="structural",
        layer_config=layer,
        now=NOW,
    )
    assert evidence["expires_at"] == (NOW + timedelta(minutes=1)).isoformat()

    with pytest.raises(StrategySettingsValidationError, match="HASH_INVALID"):
        StrategySettingsService._assert_coverage_envelope(
            _coverage_payload(
                source_timestamp=NOW - timedelta(minutes=15),
                envelope_hash="tampered",
            ),
            symbol="BTC_USDT",
            timeframe="15m",
            scheduler_group="structural",
            layer_config=layer,
            now=NOW,
        )

    wrong_config = _coverage_payload(
        source_timestamp=NOW - timedelta(minutes=15)
    )
    wrong_config["adx"]["config_hash"] = "d" * 64
    wrong_config["adx"]["envelope_hash"] = canonical_hash({
        key: value for key, value in wrong_config["adx"].items()
        if key != "envelope_hash"
    })
    with pytest.raises(StrategySettingsValidationError, match="CONFIG_HASH_REJECTED"):
        StrategySettingsService._assert_coverage_envelope(
            wrong_config,
            symbol="BTC_USDT",
            timeframe="15m",
            scheduler_group="structural",
            layer_config=layer,
            now=NOW,
        )


def test_pipeline_mtf_filter_uses_exact_layer_snapshot_not_flat_values():
    profile = {
        "default_timeframe": "1h",
        "mtf_layer": {
            "layer": "L1",
            "activation_mode": "SHADOW",
            "operational_effect": False,
        },
        "filters": {
            "logic": "AND",
            "conditions": [
                {
                    "field": "rsi",
                    "operator": ">",
                    "value": 50,
                    "timeframe": "1h",
                }
            ],
        },
    }
    asset = {
        "symbol": "BTC_USDT",
        "indicators": {"rsi": 80},
        "_indicators_by_tf": {"1h": {"rsi": 20}},
        "_score": 100,
    }

    passed, filtered = _apply_level_filter([asset], profile, "L1")

    assert passed == []
    assert filtered == []


def test_pipeline_mtf_filter_fails_closed_when_exact_snapshot_is_missing():
    profile = {
        "default_timeframe": "15m",
        "mtf_layer": {
            "layer": "L2",
            "activation_mode": "SHADOW",
            "operational_effect": False,
        },
        "filters": {
            "logic": "AND",
            "conditions": [
                {
                    "field": "adx",
                    "operator": ">=",
                    "value": 20,
                    "timeframe": "15m",
                }
            ],
        },
    }
    asset = {
        "symbol": "BTC_USDT",
        "indicators": {"adx": 40},
        "_indicators_by_tf": {},
        "_score": 100,
    }

    passed, filtered = _apply_level_filter([asset], profile, "L2")

    assert passed == []
    assert filtered == []


def test_activation_coverage_rejects_expired_context():
    layer = {
        "validity_margin_seconds": 30,
        "source_policies": {"ohlcv": {
            "allowed_source_providers": ["gate.io"],
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "allowed_capture_contract_versions": ["gate_ohlcv_canonical_v1"],
        }},
        "required_indicators_by_group": {"structural": ["adx"]},
    }
    with pytest.raises(StrategySettingsValidationError, match="CONTEXT_EXPIRED"):
        StrategySettingsService._assert_coverage_envelope(
            _coverage_payload(source_timestamp=NOW - timedelta(minutes=16)),
            symbol="BTC_USDT",
            timeframe="15m",
            scheduler_group="structural",
            layer_config=layer,
            now=NOW,
        )


def test_shadow_waiver_allows_only_value_unavailable_as_observational_gap():
    contract = {
        "activation_mode": "SHADOW",
        "operational_effect": False,
        "statistical_gate": {
            "status": "WAIVED_FOR_SHADOW",
            "authorization_scope": "OBSERVATIONAL_ONLY",
        },
    }

    assert StrategySettingsService._waiver_allows_value_unavailable(
        contract,
        "15m_STETH_USDT_structural_volume_spike_VALUE_UNAVAILABLE",
    )
    assert not StrategySettingsService._waiver_allows_value_unavailable(
        contract,
        "15m_STETH_USDT_structural_HASH_INVALID",
    )
    assert not StrategySettingsService._waiver_allows_value_unavailable(
        {**contract, "operational_effect": True},
        "15m_STETH_USDT_structural_volume_spike_VALUE_UNAVAILABLE",
    )
    assert not StrategySettingsService._waiver_allows_value_unavailable(
        {**contract, "statistical_gate": {}},
        "15m_STETH_USDT_structural_volume_spike_VALUE_UNAVAILABLE",
    )


def test_walk_forward_requires_governed_minimum_sample_config():
    with pytest.raises(MTFCalibrationConfigRequired, match="min_samples"):
        require_calibration_config({})


def test_walk_forward_folds_are_chronological_and_point_in_time():
    rows = [{"decision_at": index} for index in range(12)]
    folds = chronological_folds(
        rows, train_size=4, test_size=2, fold_count=3
    )
    assert len(folds) == 3
    for train, test in folds:
        assert max(row["decision_at"] for row in train) < min(
            row["decision_at"] for row in test
        )


def test_walk_forward_requires_oos_gain_without_worse_worst_fold_drawdown():
    baseline = [
        FoldResult(net_expectancy=1.0, max_drawdown=2.0, samples=60),
        FoldResult(net_expectancy=1.0, max_drawdown=2.0, samples=60),
    ]
    candidates = {
        "better_but_riskier": [
            FoldResult(net_expectancy=2.0, max_drawdown=3.0, samples=60),
            FoldResult(net_expectancy=2.0, max_drawdown=2.5, samples=60),
        ],
        "eligible": [
            FoldResult(net_expectancy=1.5, max_drawdown=1.5, samples=60),
            FoldResult(net_expectancy=1.4, max_drawdown=2.0, samples=60),
        ],
    }
    assert select_candidate(candidates, baseline_folds=baseline) == "eligible"
