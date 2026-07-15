from datetime import datetime, timedelta, timezone

import pytest
import numpy as np

from app.ml.feature_contract_v2 import normalize_snapshot, snapshot_hash, temporal_contract_errors
from app.ml.grouped_purged_split import TemporalObservation, grouped_purged_split
from app.ml.model_governance import can_publish_ml_evidence, governance_from_gate
from app.services.profile_versioning_v2 import content_hash
from app.ml.economic_targets import OutcomeCosts, economic_label, expected_value_pct
from app.services.ml_challenger_service import MLChallengerService


def test_descriptive_findings_never_grant_predictive_authority():
    governance = governance_from_gate(
        descriptive_gate={"status": "APPROVED"},
        predictive_gate={"status": "REJECTED"},
    )
    assert governance["descriptive_status"] == "DESCRIPTIVE_VALIDATED"
    assert governance["predictive_status"] == "PREDICTIVE_REJECTED"
    assert governance["calibration_authority"] is False
    assert governance["rule_generation_authority"] is False
    assert governance["execution_authority"] is False


def test_v77_cannot_publish_ml_evidence():
    allowed, reasons = can_publish_ml_evidence({
        "id": "e3dd7497-0747-4132-84b3-98571bd4b7f3",
        "predictive_status": "PREDICTIVE_REJECTED",
        "calibration_authority": False,
        "rule_generation_authority": False,
    })
    assert allowed is False
    assert "predictive_model_not_approved_for_intelligence" in reasons


def test_feature_aliases_and_categories_are_canonical():
    normalized, errors = normalize_snapshot({
        "atr_percent": 1.25,
        "macd_signal": "positive",
        "psar_trend": "bearish",
        "di_plus": 24,
        "di_minus": 18,
        "volume_24h": 42,
        "bb_width": 0.08,
    })
    assert errors == []
    assert "atr_percent" not in normalized
    assert normalized["atr_pct"] == 1.25
    assert normalized["macd_signal"] == "bullish"
    assert normalized["psar_trend"] == "FALLING"
    assert normalized["di_trend"] == "bullish"
    assert normalized["volume_24h_base"] == 42
    assert snapshot_hash(normalized) == snapshot_hash(dict(reversed(list(normalized.items()))))


def test_temporal_contract_rejects_future_features_and_early_label():
    now = datetime.now(timezone.utc)
    errors = temporal_contract_errors(
        feature_source_at=now,
        features_captured_at=now + timedelta(seconds=5),
        decision_created_at=now,
        entry_at=now + timedelta(seconds=10),
        label_resolved_at=now,
    )
    assert "features_after_decision" in errors
    assert "label_not_after_decision" in errors


def test_grouped_split_purges_both_boundaries_and_group_overlap():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = [
        TemporalObservation("a", "g1", start, start + timedelta(hours=1), "t1"),
        # label crosses validation boundary and must be purged from train
        TemporalObservation("b", "g2", start + timedelta(days=1), start + timedelta(days=3, hours=1), "t2"),
        TemporalObservation("c", "g3", start + timedelta(days=3), start + timedelta(days=3, hours=1), "t3"),
        # label crosses test boundary and must be purged from validation
        TemporalObservation("d", "g4", start + timedelta(days=4), start + timedelta(days=5, hours=1), "t4"),
        # repeated group in test removes its validation copy
        TemporalObservation("e", "shared", start + timedelta(days=4), start + timedelta(days=4, hours=1), "t5"),
        TemporalObservation("f", "shared", start + timedelta(days=6), start + timedelta(days=6, hours=1), "t6"),
    ]
    split = grouped_purged_split(
        rows,
        validation_start=start + timedelta(days=3),
        test_start=start + timedelta(days=5),
        label_horizon=timedelta(hours=1),
        embargo=timedelta(hours=12),
    )
    assert {row.row_id for row in split.train} == {"a"}
    assert {row.row_id for row in split.validation} == {"c"}
    assert {row.row_id for row in split.test} == {"f"}
    assert split.diagnostics["group_overlap"] == 0
    assert split.diagnostics["label_horizon_overlap"] == 0


def test_invalid_boundary_order_fails():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="validation_start_must_precede_test_start"):
        grouped_purged_split([], validation_start=now, test_start=now, label_horizon=timedelta(), embargo=timedelta())


def test_profile_version_hash_is_order_independent():
    assert content_hash({"signals": {"min": 1}, "threshold": 70}) == content_hash(
        {"threshold": 70, "signals": {"min": 1}}
    )


def test_economic_target_accounts_for_all_costs_and_neutral_band():
    outcome = OutcomeCosts(0.30, fees_pct=0.10, spread_pct=0.05, slippage_pct=0.05)
    assert outcome.net_return_pct == pytest.approx(0.10)
    assert economic_label(outcome, noise_band_pct=0.15) is None
    assert economic_label(outcome, noise_band_pct=0.05) == 1
    assert expected_value_pct(
        p_tp=0.5, avg_tp_pct=1.0,
        p_sl=0.3, avg_sl_pct=-1.0,
        p_timeout=0.2, avg_timeout_pct=0.0,
        costs_pct=0.1,
    ) == pytest.approx(0.1)


def test_operational_split_purges_validation_test_and_duplicate_groups():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    times = [start + timedelta(hours=i) for i in range(30)]
    holding = [0] * 30
    holding[20] = 10 * 3600  # validation label resolves inside test
    groups = [f"g{i}" for i in range(30)]
    groups[19] = "shared"
    groups[25] = "shared"
    result = MLChallengerService._chronological_split_with_embargo(
        np.arange(30).reshape(-1, 1),
        np.asarray([i % 2 for i in range(30)]),
        metadata=[list(range(30))],
        created_at=times,
        holding_seconds=holding,
        embargo_seconds=1,
        group_ids=groups,
    )
    assert result["n_purged_val_test"] == 1
    assert result["n_group_purged"] >= 1
    assert result["split_diagnostics"]["group_overlap"] == 0
    assert result["split_diagnostics"]["label_horizon_overlap"] == 0
    diagnostics = result["split_diagnostics"]
    train_times = [times[i] for i in result["meta_tr"][0]]
    validation_times = [times[i] for i in result["meta_va"][0]]
    test_times = [times[i] for i in result["meta_te"][0]]
    assert diagnostics["dataset_rows"] == 30
    assert diagnostics["dataset_from"] == times[0].isoformat()
    assert diagnostics["dataset_to"] == times[-1].isoformat()
    assert diagnostics["train_from"] == min(train_times).isoformat()
    assert diagnostics["train_to"] == max(train_times).isoformat()
    assert diagnostics["validation_from"] == min(validation_times).isoformat()
    assert diagnostics["validation_to"] == max(validation_times).isoformat()
    assert diagnostics["test_from"] == min(test_times).isoformat()
    assert diagnostics["test_to"] == max(test_times).isoformat()
    assert diagnostics["effective_train_samples"] == len(result["y_tr"])
    assert diagnostics["effective_validation_samples"] == len(result["y_va"])
    assert diagnostics["effective_test_samples"] == len(result["y_te"])
    assert "shared" not in {
        groups[i] for i in result["meta_va"][0]
    }


def test_operational_split_fails_closed_without_feasible_boundaries():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    times = [start + timedelta(hours=i) for i in range(12)]
    result = MLChallengerService._chronological_split_with_embargo(
        np.arange(12).reshape(-1, 1),
        np.asarray([i % 2 for i in range(12)]),
        metadata=[list(range(12))],
        created_at=times,
        holding_seconds=[0] * 12,
        embargo_seconds=3600,
        group_ids=[f"g{i}" for i in range(12)],
        min_train_size=20,
        min_validation_size=5,
        min_test_size=5,
    )

    assert len(result["y_va"]) == 0
    assert result["y_te"] is None
    assert result["split_diagnostics"]["split_strategy"] == (
        "grouped_purged_no_feasible_boundaries"
    )
    assert result["split_diagnostics"]["required_test_samples"] == 5
    assert result["split_diagnostics"]["test_sample_deficit"] == 5
    assert result["split_diagnostics"]["dataset_rows"] == 12
    assert result["split_diagnostics"]["dataset_from"] == times[0].isoformat()
    assert result["split_diagnostics"]["dataset_to"] == times[-1].isoformat()
    assert result["split_diagnostics"]["train_from"] is None
    assert result["split_diagnostics"]["validation_from"] is None
    assert result["split_diagnostics"]["test_from"] is None


def test_operational_split_enforces_promotion_holdout_minimum():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    times = [start + timedelta(minutes=i) for i in range(80)]
    result = MLChallengerService._chronological_split_with_embargo(
        np.arange(80).reshape(-1, 1),
        np.asarray([i % 2 for i in range(80)]),
        metadata=[list(range(80))],
        created_at=times,
        holding_seconds=[0] * 80,
        embargo_seconds=60,
        group_ids=[f"g{i}" for i in range(80)],
        min_train_size=40,
        min_validation_size=10,
        min_test_size=40,
    )

    assert result["has_test"] is False
    assert result["split_diagnostics"]["required_test_samples"] == 40
    assert result["split_diagnostics"]["max_candidate_test_samples"] < 40
    assert result["split_diagnostics"]["test_sample_deficit"] > 0


def test_operational_split_rejects_single_class_validation():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    times = [start + timedelta(minutes=i) for i in range(60)]
    labels = np.asarray(([0, 1] * 15) + ([1] * 10) + ([0, 1] * 10))
    result = MLChallengerService._chronological_split_with_embargo(
        np.arange(60).reshape(-1, 1),
        labels,
        metadata=[list(range(60))],
        created_at=times,
        holding_seconds=[0] * 60,
        embargo_seconds=0,
        group_ids=[f"g{i}" for i in range(60)],
        min_train_size=30,
        min_validation_size=10,
        min_test_size=20,
    )

    assert result["has_test"] is False
    assert result["split_diagnostics"]["requires_class_diversity"] is True
    assert result["split_diagnostics"]["single_class_candidates"] > 0
