from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.mtf_observation_service import (
    _direction,
    advance_l2_setup_state,
    build_l3_confirmation,
)
from app.services.mtf_walk_forward import (
    MTFCalibrationConfigRequired,
    candidate_grid,
    chronological_folds,
    evaluate_candidate,
    evaluate_returns,
    fit_candidate,
    require_calibration_config,
)
from app.services.mtf_calibration_service import _l2_temporal_eligibility
from app.tasks.compute_mtf_indicators import required_warmup_candles
from app.tasks.collect_mtf_ohlcv import collection_fetch_limit


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
BACKEND = Path(__file__).resolve().parents[1]


def _l2_values(price=99.5, reclaim=False):
    return {
        "price": price, "atr": 2.0, "ema21": 100.0, "ema50": 99.0,
        "vwap": 100.0, "vwap_reclaim_bool": reclaim,
        "bb_upper": 103.0, "bb_lower": 97.0,
        "di_plus": 30.0, "di_minus": 10.0,
        "higher_highs_5": True, "higher_lows_5": True,
    }


SEMANTICS = {
    "max_extension_atr": 2.0,
    "pullback_max_distance_atr": 1.0,
    "breakout_min_distance_atr": 0.2,
    "retest_tolerance_atr": 0.3,
    "invalidation_atr": 0.5,
    "setup_valid_candles": 3,
}


def test_equal_ema_and_di_are_neutral_not_bearish():
    assert _direction({
        "di_plus": 10, "di_minus": 10, "ema21": 100, "ema50": 100,
        "ema21_slope_pct": 0, "ema50_slope_pct": 0,
        "higher_highs_5": True, "higher_lows_5": False,
    }) == "NEUTRAL"


def test_l2_requires_prior_candle_and_replay_is_deterministic():
    first = advance_l2_setup_state(
        values=_l2_values(), candle_open_at=NOW - timedelta(minutes=30),
        semantics=SEMANTICS, previous=None,
    )
    assert first["state"] == "PULLBACK_SEEN"
    replay = advance_l2_setup_state(
        values=_l2_values(), candle_open_at=NOW - timedelta(minutes=30),
        semantics=SEMANTICS, previous=first,
    )
    assert replay["state"] == "PULLBACK_SEEN"
    second = advance_l2_setup_state(
        values=_l2_values(price=101, reclaim=True),
        candle_open_at=NOW - timedelta(minutes=15),
        semantics=SEMANTICS, previous=first,
    )
    assert second["state"] == "PULLBACK_RECLAIM"
    with pytest.raises(ValueError, match="REPLAY_CONFLICT"):
        advance_l2_setup_state(
            values=_l2_values(price=100.5),
            candle_open_at=NOW - timedelta(minutes=30),
            semantics=SEMANTICS, previous=first,
        )


def test_l2_sequence_expires_across_missing_candles():
    first = advance_l2_setup_state(
        values=_l2_values(), candle_open_at=NOW - timedelta(minutes=60),
        semantics={**SEMANTICS, "setup_valid_candles": 2}, previous=None,
    )
    after_gap = advance_l2_setup_state(
        values=_l2_values(price=101, reclaim=True),
        candle_open_at=NOW - timedelta(minutes=15),
        semantics={**SEMANTICS, "setup_valid_candles": 2}, previous=first,
    )
    assert after_gap["state"] == "NONE"


def test_l3_missing_temporal_metadata_is_unavailable():
    confirmation = build_l3_confirmation(
        legacy_decision="ALLOW",
        indicators_snapshot={"rsi": {"value": 55}},
        gate_evaluation_hash="a" * 64,
        layer_config={
            "validity_margin_seconds": 30,
            "required_indicators_by_group": {"structural": ["rsi"]},
            "source_policies": {"ohlcv": {
                "allowed_source_providers": ["gate.io"],
                "provider_policy_id": "spot_gate_closed_ohlcv_v1",
                "allowed_capture_contract_versions": ["gate_ohlcv_canonical_v1"],
            }},
        },
        now=NOW,
    )
    assert confirmation["verdict"] == "UNAVAILABLE"
    assert confirmation["invalid_indicators"] == ["rsi"]


def test_walk_forward_excludes_outcomes_crossing_test_boundary():
    rows = [
        {
            "id": index,
            "decision_at": NOW + timedelta(minutes=index),
            "outcome_at": NOW + timedelta(minutes=index + (10 if index == 3 else 0)),
        }
        for index in range(10)
    ]
    folds = chronological_folds(rows, train_size=4, test_size=2, fold_count=1)
    train, test = folds[0]
    assert all(row["outcome_at"] < test[0]["decision_at"] for row in train)


def test_candidate_threshold_is_fit_from_training_only():
    template = {
        "id": "L1:adx:min@q0.500000",
        "rules": [{"layer": "L1", "feature": "L1.adx", "operator": "min", "quantile": 0.5}],
    }
    train = [{"features": {"L1.adx": value}} for value in (10, 20, 30)]
    fitted = fit_candidate(template, train)
    assert fitted["rules"][0]["threshold"] == 20
    train.append({"features": {"L1.adx": 10_000}})
    assert fitted["rules"][0]["threshold"] == 20


def test_simultaneous_decisions_stay_on_test_side_of_fold_boundary():
    rows = [
        {
            "id": index,
            "decision_at": NOW + timedelta(minutes=5 if index in {4, 5} else index),
            "outcome_at": NOW + timedelta(minutes=index + 0.5),
        }
        for index in range(10)
    ]
    train, test = chronological_folds(
        rows, train_size=4, test_size=5, fold_count=1,
    )[0]
    assert not ({row["decision_at"] for row in train} & {row["decision_at"] for row in test})


def test_candidate_expectancy_uses_same_population_as_baseline():
    rows = [
        {"net_return": 10.0, "features": {"L1.adx": 30.0}},
        {"net_return": -2.0, "features": {"L1.adx": 10.0}},
    ]
    candidate = {
        "rules": [{"feature": "L1.adx", "operator": "min", "threshold": 20.0}]
    }
    result = evaluate_candidate(candidate, rows)
    baseline = evaluate_returns(rows)
    assert result.samples == 1
    assert result.population_samples == baseline.population_samples == 2
    assert result.net_expectancy == 5.0


def test_mtf_warmup_is_derived_from_active_periods_and_timeframe():
    config = {
        "rsi": {"enabled": True, "period": 14},
        "adx": {"enabled": True, "period": 14},
        "ema": {"enabled": True, "periods": [21, 50, 200]},
        "atr": {"enabled": True, "period": 14},
        "macd": {"enabled": True, "slow": 26, "signal": 9},
        "bollinger": {"enabled": True, "period": 20},
    }
    assert required_warmup_candles(config, "1h") == 201
    assert required_warmup_candles(config, "15m") == 201
    config["ema"]["periods"] = [21, 50]
    assert required_warmup_candles(config, "15m") == 96


def test_mtf_collection_reserves_headroom_for_open_candle():
    config = {"ema": {"enabled": True, "periods": [21, 50, 200]}}

    assert required_warmup_candles(config, "15m") == 201
    assert collection_fetch_limit(config, "15m") == 202


def test_mtf_warmup_rejects_missing_enabled_period_config():
    with pytest.raises(ValueError, match="CONFIG_REQUIRED:ema"):
        required_warmup_candles({"ema": {"enabled": True}}, "1h")


def test_calibration_requires_governed_dataset_query_limits():
    with pytest.raises(
        MTFCalibrationConfigRequired,
        match="max_dataset_rows,dataset_query_timeout_ms",
    ):
        require_calibration_config({
            "policy_version": "proposal-v1",
            "approval_status": "APPROVED",
            "approved_at": NOW.isoformat(),
            "approved_by": "00000000-0000-0000-0000-000000000001",
            "min_samples": 1,
            "fold_count": 1,
            "train_window_rows": 1,
            "test_window_rows": 1,
            "embargo_rows": 0,
            "validation_holdout_rows": 1,
            "min_test_samples_per_fold": 1,
            "candidate_quantiles": [0.5],
            "candidate_dimensions": [],
            "max_candidates": 1,
            "confidence_level": 0.95,
            "observation_min_samples": 1,
            "observation_min_hours": 1,
            "cost_field": "fee_roundtrip_pct_applied",
            "return_field": "pnl_pct",
            "profile_templates": {},
            "scope": {},
            "l2_history_candles": 2,
            "sampling_unit": "SHADOW_DECISION",
            "overlap_policy": "PURGE_TRAIN_OUTCOMES_AT_TEST_BOUNDARY",
            "capital_policy": {},
            "execution_policy": {},
        })


def test_indicator_identity_index_is_concurrent_additive_and_reversible():
    source = (BACKEND / "alembic/versions/217_indicator_identity_latest_index.py").read_text(
        encoding="utf-8"
    )
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in source
    assert "symbol, market_type, timeframe, scheduler_group, time DESC" in source
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in source
    assert "DROP TABLE" not in source


def test_watchlist_reads_do_not_sort_the_entire_indicators_table():
    for path in (
        BACKEND / "app/api/watchlist.py",
        BACKEND / "app/api/profiles.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "FROM indicators\n            ORDER BY symbol, time DESC" not in source
        assert "fetch_merged_indicators" in source


def test_mtf_collector_uses_the_production_ohlcv_identity():
    source = (BACKEND / "app/tasks/collect_mtf_ohlcv.py").read_text(encoding="utf-8")
    assert "ON CONFLICT (time, symbol, exchange, timeframe)" in source


def test_mtf_collectors_use_the_isolated_research_queue():
    from app.tasks.celery_app import TASK_ROUTES, celery_app

    for suffix, schedule_name in (
        ("collect_15m", "collect_mtf_15m_after_close"),
        ("collect_1h", "collect_mtf_1h_after_close"),
    ):
        task_name = f"app.tasks.collect_mtf_ohlcv.{suffix}"
        assert TASK_ROUTES[task_name]["queue"] == "research_ohlcv"
        assert celery_app.conf.beat_schedule[schedule_name]["options"]["queue"] == "research_ohlcv"


def test_isolated_research_worker_imports_mtf_collectors(monkeypatch):
    from app.tasks import celery_app as celery_app_module

    monkeypatch.setenv("WORKER_QUEUES", "research_ohlcv")

    assert "app.tasks.collect_mtf_ohlcv" in celery_app_module._configured_task_modules()


def test_discrete_semantics_are_fitted_without_reading_test_data():
    policy = {
        "candidate_quantiles": [0.5],
        "max_candidates": 2,
        "candidate_dimensions": [{
            "layer": "L2",
            "mode": "discrete",
            "applies_to": "SEMANTIC",
            "semantic_key": "setup_valid_candles",
            "values": [2, 3],
        }],
    }
    candidates = candidate_grid(policy)
    assert len(candidates) == 2
    assert {fit_candidate(item, [])["rules"][0]["threshold"] for item in candidates} == {2, 3}


def test_candidate_l2_eligibility_replays_a_real_two_candle_sequence():
    rules = [
        {
            "layer": "L2", "mode": "discrete", "applies_to": "SEMANTIC",
            "semantic_key": key, "value": value, "threshold": value,
        }
        for key, value in SEMANTICS.items()
    ]
    row = {
        "l2_history": [
            {
                "candle_open_at": NOW - timedelta(minutes=30),
                "values": _l2_values(),
            },
            {
                "candle_open_at": NOW - timedelta(minutes=15),
                "values": _l2_values(price=101, reclaim=True),
            },
        ]
    }
    assert _l2_temporal_eligibility({"rules": rules}, row) is True
