from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import numpy as np
import pytest

from app.ml.l3_integrity import (
    build_inference_frame, market_event_key, holdout_statistics,
    stable_profile_bucket, contract_definitions, project_capture,
)
from app.services.ml_challenger_service import _filter_l3_barrier_contract, _calibrate_ev_threshold


def test_nonprefix_features_use_artifact_order_and_preserve_missing():
    model = SimpleNamespace(_inference_feature_names=["adx", "rsi", "volume_delta"], _n_inference_features=3)
    vector = build_inference_frame(model, {"rsi": 61, "adx": 23})
    assert vector[0, :2].tolist() == [23, 61]
    assert np.isnan(vector[0, 2])


@pytest.mark.parametrize("names", [None, [], ["rsi", "rsi"], ["outcome"], ["unknown"]])
def test_invalid_schema_is_not_guessed(names):
    with pytest.raises(ValueError):
        build_inference_frame(SimpleNamespace(_inference_feature_names=names), {"rsi": 61})


def test_required_missing_feature_is_not_a_valid_prediction():
    model = SimpleNamespace(_inference_feature_names=["adx"], _required_feature_names=["adx"])
    with pytest.raises(ValueError, match="required_feature"):
        build_inference_frame(model, {})


def test_profile_encoding_and_categorical_subset():
    model = SimpleNamespace(_inference_feature_names=["profile_id_encoded", "source_encoded", "rsi"], get_cat_feature_indices=lambda: [1])
    frame = build_inference_frame(model, {"rsi": 50}, "profile-example")
    assert frame.iloc[0].tolist() == [stable_profile_bucket("profile-example"), "1", 50]


def test_market_event_identity_ignores_profile_snapshot_but_not_time():
    event = dict(symbol="BTC_USDT", exchange="gate", timeframe="5m", entry_timestamp="2026-08-01T12:00:00Z")
    assert market_event_key({**event, "profile_id": "a", "features_snapshot": {"rsi": 50}}) == market_event_key({**event, "profile_id": "b", "exchange": "gate.io"})
    assert market_event_key(event) != market_event_key({**event, "entry_timestamp": "2026-08-01T12:05:00Z"})
    with pytest.raises(ValueError):
        market_event_key({"symbol": "BTC_USDT"})


def test_active_v3_does_not_admit_v2_and_other_lanes_keep_legacy_default():
    rows = [dict(barrier_mode="ATR_DYNAMIC", tp_pct_applied=2, barrier_contract_version=v) for v in ["shadow_atr_dynamic_v2", "shadow_atr_dynamic_v3"]]
    kept, meta = _filter_l3_barrier_contract(rows, expected_mode="ATR_DYNAMIC", expected_tp_pct=.6, expected_contract_version="shadow_atr_dynamic_v3")
    assert kept == [rows[1]]
    assert meta["barrier_contract_version_mismatch"] == 1
    legacy, _ = _filter_l3_barrier_contract(rows, expected_mode="ATR_DYNAMIC", expected_tp_pct=.6)
    assert legacy == [rows[0]]


def test_holdout_counts_events_and_days_and_is_reproducible():
    times = [datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(days=i // 4) for i in range(24)]
    labels = [0, 0, 1, 1] * 6
    groups = [str(i // 2) for i in range(24)]
    probabilities = [.2, .2, .8, .8] * 6
    args = (labels, probabilities, [-1, -1, 1, 1] * 6, .5, times, groups)
    result = holdout_statistics(*args, iterations=30, seed=42)
    assert result == holdout_statistics(*args, iterations=30, seed=42)
    assert result["distinct_days"] == 6
    assert result["independent_events"] == 12
    assert result["selected_events"] == 6
    assert result["roc_auc_ci_low"] == 1
    assert result["net_ev"] == 1


def test_one_day_uncertainty_is_unavailable_not_optimistic():
    r = holdout_statistics([0, 1], [.1, .9], [-1, 1], .5,
        [datetime(2026, 8, 1, tzinfo=timezone.utc)] * 2, ["a", "b"], iterations=10, seed=1)
    assert r["roc_auc_ci_low"] is None
    assert r["uncertainty_unavailable_reason"]


def test_threshold_minimum_uses_independent_weight():
    with pytest.raises(ValueError, match="no_eligible_threshold"):
        _calibrate_ev_threshold([.9] * 10, [1] * 10, .1, 3, weights=[.1] * 10)


def test_contract_hash_changes_with_economics_and_feature_order():
    cfg = dict(ml_label_version="positive_net_return_v1", ml_label_objective="positive_net_return",
        ml_active_barrier_contract_version="shadow_atr_dynamic_v3", ml_fee_roundtrip_pct=.2, ml_win_fast_threshold_seconds=14400)
    base = contract_definitions(cfg, ["rsi", "adx"])
    assert base["dataset_id"] != contract_definitions({**cfg, "ml_fee_roundtrip_pct": .3}, ["rsi", "adx"])["dataset_id"]
    assert base["feature_id"] != contract_definitions(cfg, ["adx", "rsi"])["feature_id"]


def test_observational_fields_do_not_poison_ml_but_required_score_is_not_neutralized():
    raw = {"score": 80, "rsi": 51, "signal_score": 70, "ema_full_alignment": True}
    config = {"ml_feature_contract": {"L3_PROFILE": {"required": ["rsi"]}}}
    projected, meta = project_capture(raw, {"rsi": {"ts": "2026-08-01T12:00:00Z"}}, config)
    assert projected == {"rsi": 51, "signal_score": None}
    assert raw["signal_score"] == 70
    assert meta["optional_without_provenance"] == ["signal_score"]
    config["ml_feature_contract"]["L3_PROFILE"]["required"].append("signal_score")
    projected, _ = project_capture(raw, {}, config)
    assert projected["signal_score"] == 70


def test_future_score_timestamp_is_never_neutralized_to_hide_violation():
    raw = {"signal_score": 70}
    projected, _ = project_capture(raw, {"signal_score": {"ts": "2099-01-01T00:00:00Z"}}, {})
    assert projected == raw


def test_catboost_fit_emits_independent_holdout_statistics():
    from app.services.ml_challenger_service import _train_catboost_sync
    x = np.asarray([[i % 2, i % 3] for i in range(60)], dtype=float)
    y = np.asarray([i % 2 for i in range(60)])
    times = [datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(days=i // 10) for i in range(60)]
    result = _train_catboost_sync(x, y, x, y, ["rsi", "adx"],
        X_test=x, y_test=y, val_returns=y * 2 - 1, test_returns=y * 2 - 1,
        threshold_min_positives=1, fixed_params={"iterations": 5, "depth": 2, "thread_count": 1},
        test_times=times, test_groups=[str(i) for i in range(60)], bootstrap_iterations=10)
    stats = result["test_metrics"]
    assert stats["independent_events"] == 60
    assert stats["distinct_days"] == 6
    assert stats["roc_auc_ci_low"] is not None
    assert stats["bootstrap_valid_iterations"] == 10
