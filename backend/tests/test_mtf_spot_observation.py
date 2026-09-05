from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.layer_context import CandleIdentity, ProfileIdentity
from app.services.mtf_observation_service import (
    build_l1_context,
    build_l2_context,
    build_l3_confirmation,
    build_multilayer_context,
    verify_context_hash,
)
from app.services.multilayer_contract import require_shadow_multilayer_config
from app.services.profile_engine import ProfileEngine
from app.services.profile_runtime_config import canonical_hash
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
        "mtf_semantics": {"max_extension_atr": 2.0},
    }
    l2_values = {
        "price": 101,
        "atr": 2,
        "ema21": 100,
        "ema50": 99,
        "vwap": 100,
        "vwap_reclaim_bool": True,
        "bb_upper": 103,
        "bb_lower": 97,
        "di_plus": 30,
        "di_minus": 10,
        "higher_highs_5": True,
        "higher_lows_5": True,
    }
    l2 = build_l2_context(
        symbol="BTC_USDT",
        profile=l2_profile,
        profile_identity=IDENTITY,
        values=l2_values,
        candle=_candle("15m"),
        expires_at=NOW + timedelta(minutes=5),
        l1_context=l1,
        now=NOW,
    )
    aggregate = build_multilayer_context(
        l1=l1,
        l2=l2,
        l3_confirmation=build_l3_confirmation(
            legacy_decision="ALLOW",
            indicators_snapshot={},
            gate_evaluation_hash="c" * 64,
            now=NOW,
        ),
        canonical_score=70,
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
            }},
        }
    config = {
        "multilayer_contract": {
            "enabled": True,
            "activation_mode": "SHADOW",
            "operational_effect": False,
            "decision_feature_contract_version": "multilayer_decision_context_v2",
            "layers": layers,
        }
    }
    assert require_shadow_multilayer_config(config)["operational_effect"] is False
    config["multilayer_contract"]["operational_effect"] = True
    with pytest.raises(ValueError, match="FORBIDDEN"):
        require_shadow_multilayer_config(config)


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
        "config_hash": "c" * 64,
        "producer_version": "mtf_indicator_producer_v1",
    }
    envelope["envelope_hash"] = envelope_hash or canonical_hash(envelope)
    return {"adx": envelope}


def test_activation_coverage_recomputes_hash_and_derives_expiry_from_contract():
    layer = {
        "validity_margin_seconds": 60,
        "source_policies": {"ohlcv": {
            "allowed_source_providers": ["gate.io"],
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
        }},
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


def test_activation_coverage_rejects_expired_context():
    layer = {
        "validity_margin_seconds": 30,
        "source_policies": {"ohlcv": {
            "allowed_source_providers": ["gate.io"],
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
        }},
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
