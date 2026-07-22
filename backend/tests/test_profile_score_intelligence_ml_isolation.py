from pathlib import Path

import pytest

from app.services.profile_score_intelligence_service import (
    ScorePolicy,
    _difference,
    distribution,
    score_statistics,
    threshold_metrics,
)


POLICY = ScorePolicy(
    min_total_closed_trades=3,
    min_outcome_trades=1,
    min_field_coverage=0.5,
    min_distinct_symbols=1,
    min_distinct_days=1,
    max_single_symbol_share=1.0,
    max_single_day_share=1.0,
)


def _rows():
    return [
        {"outcome": "TP_HIT", "score": 80, "momentum_score": 75, "pnl_pct": 1.0, "mae_pct": -0.2, "mfe_pct": 1.2, "holding_seconds": 100, "symbol": "A", "created_at": "2026-07-20"},
        {"outcome": "TP_HIT", "score": 90, "momentum_score": 85, "pnl_pct": 2.0, "mae_pct": -0.1, "mfe_pct": 2.2, "holding_seconds": 200, "symbol": "B", "created_at": "2026-07-21"},
        {"outcome": "SL_HIT", "score": 40, "momentum_score": 45, "pnl_pct": -1.0, "mae_pct": -1.2, "mfe_pct": 0.1, "holding_seconds": 300, "symbol": "A", "created_at": "2026-07-20"},
        {"outcome": "SL_HIT", "score": 50, "momentum_score": None, "pnl_pct": -2.0, "mae_pct": -2.2, "mfe_pct": 0.2, "holding_seconds": 400, "symbol": "C", "created_at": "2026-07-22"},
        {"outcome": "TIMEOUT", "score": 60, "momentum_score": 55, "pnl_pct": 0.2, "mae_pct": -0.4, "mfe_pct": 0.5, "holding_seconds": 500, "symbol": "D", "created_at": "2026-07-22"},
    ]


def test_score_statistics_keep_null_missing_and_separate_all_outcomes():
    stats = {item["score"]: item for item in score_statistics(_rows(), POLICY)}
    score = stats["score"]
    momentum = stats["momentum_score"]

    assert score["tp"] == {"n": 2, "min": 80.0, "p25": 82.5, "median": 85.0, "mean": 85.0, "p75": 87.5, "p90": 89.0, "max": 90.0}
    assert score["sl"]["mean"] == 45.0
    assert score["timeout"]["n"] == 1
    assert score["delta_mean_tp_sl"] == 40.0
    assert score["auc"] == 1.0
    assert score["ks_statistic"] == 1.0
    assert momentum["missing"] == 1
    assert momentum["present"] == 4
    assert momentum["coverage"] == 0.8
    assert momentum["sl"]["n"] == 1


def test_threshold_simulator_is_read_only_math_with_timeout_and_volume_delta():
    result = threshold_metrics(_rows(), "score", 70)

    assert result["passed"]["trades"] == 2
    assert result["passed"]["tp"] == 2
    assert result["passed"]["sl"] == 0
    assert result["passed"]["timeout"] == 0
    assert result["eliminated_trades"] == 3
    assert result["volume_reduction"] == pytest.approx(0.6)
    assert result["passed"]["avg_pnl_pct"] == 1.5
    assert result["passed"]["avg_mae_pct"] == pytest.approx(-0.15)
    assert result["passed"]["avg_mfe_pct"] == pytest.approx(1.7)
    assert result["passed"]["avg_holding_seconds"] == 150
    assert _difference(0.65, 0.60) == pytest.approx(0.05)
    assert _difference(None, 0.60) is None


def test_buckets_are_deterministic_and_not_persisted():
    first = distribution(_rows(), "score", "quantile")
    second = distribution(_rows(), "score", "quantile")

    assert first == second
    assert first["deterministic"] is True
    assert first["persisted"] is False
    assert sum(bucket["trades"] for bucket in first["buckets"]) == 5


def test_score_intelligence_has_no_ml_or_shadow_writer_dependencies_and_no_dml():
    source_path = Path(__file__).resolve().parents[1] / "app" / "services" / "profile_score_intelligence_service.py"
    source = source_path.read_text(encoding="utf-8").lower()

    forbidden_imports = (
        "dataset_builder", "dataset_policy", "feature_extractor", "ml_challenger_service",
        "autopilot_service", "shadow_trade_service", "run_lgbm", "run_catboost", "xgboost",
        "lightgbm", "catboost",
    )
    assert all(f"import {name}" not in source and f"from {name}" not in source for name in forbidden_imports)
    assert "insert into" not in source
    assert "update shadow_trades" not in source
    assert "delete from" not in source
    assert "eligible_for_training =" not in source
    assert "ml_training_dataset" not in source
    assert "ml_models" not in source
    assert "ml_model_registry" not in source
    assert "created_candidate\": false" in source
    assert "ml_mutated\": false" in source


def test_scope_sql_enforces_owner_source_versions_hashes_and_official_contract():
    source_path = Path(__file__).resolve().parents[1] / "app" / "services" / "profile_score_intelligence_service.py"
    source = source_path.read_text(encoding="utf-8")

    for required in (
        "p.user_id=:uid", "st.user_id=:uid", "st.source=:source",
        "st.profile_id=:profile_id", "st.profile_version_id=:profile_version_id",
        "st.score_engine_version_id=:score_engine_version_id",
        "st.profile_config_hash=:profile_config_hash",
        "st.score_engine_config_hash=:score_engine_config_hash",
        "st.timeframe IS NOT DISTINCT FROM :timeframe",
        "_official_sql('st')",
    ):
        assert required in source


def test_api_exposes_only_analytics_and_explicit_read_only_simulator():
    api_path = Path(__file__).resolve().parents[1] / "app" / "api" / "profile_intelligence.py"
    source = api_path.read_text(encoding="utf-8")

    for route in (
        "/score-intelligence/overview", "/score-intelligence/distribution",
        "/score-intelligence/threshold-analysis", "/score-intelligence/version-comparison",
        "/score-intelligence/simulate-threshold",
    ):
        assert route in source
    assert "profile_score_intelligence_service.simulate" in source
