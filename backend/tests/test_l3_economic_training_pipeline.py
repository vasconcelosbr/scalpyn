import numpy as np

from backend.app.ml.feature_extractor import build_training_dataframe
from backend.app.services.ml_challenger_service import (
    _filter_l3_barrier_contract,
    _stable_train_feature_indices,
    _validation_selection_score,
)


def test_positive_net_return_label_aligns_with_promotion_ev():
    records = [
        {
            "pnl_pct": 0.4,
            "net_return_pct": 0.2,
            "outcome": "TIMEOUT",
            "holding_seconds": 20_000,
            "features_snapshot": {"rsi": 50},
        },
        {
            "pnl_pct": -0.4,
            "net_return_pct": -0.6,
            "outcome": "TP_HIT",
            "holding_seconds": 100,
            "features_snapshot": {"rsi": 50},
        },
    ]

    df = build_training_dataframe(records, label_objective="positive_net_return")

    assert df["is_win_fast"].tolist() == [1, 0]
    assert df["_net_return_pct"].tolist() == [0.2, -0.6]


def test_stable_feature_filter_uses_train_coverage_and_exclusions():
    X_train = np.array([
        [1.0, np.nan, 3.0, 1.0],
        [2.0, np.nan, 3.0, 2.0],
        [3.0, 9.0, 3.0, 3.0],
        [4.0, np.nan, 3.0, 4.0],
    ])

    indices = _stable_train_feature_indices(
        X_train,
        ["healthy", "low_coverage", "constant", "excluded"],
        min_coverage=0.5,
        excluded=["excluded"],
    )

    assert indices == [0]


def test_l3_barrier_contract_rejects_mixed_payoff_policies():
    records = [
        {
            "barrier_mode": "ATR_DYNAMIC",
            "tp_pct_applied": 1.5,
            "barrier_contract_version": "shadow_atr_dynamic_v2",
            "id": "keep",
        },
        {"barrier_mode": "FIXED", "tp_pct_applied": 1.5, "id": "mode"},
        {"barrier_mode": "ATR_DYNAMIC", "tp_pct_applied": 0.6, "id": "tp"},
        {"barrier_mode": None, "tp_pct_applied": None, "id": "missing"},
    ]

    kept, meta = _filter_l3_barrier_contract(
        records,
        expected_mode="ATR_DYNAMIC",
        expected_tp_pct=1.5,
    )

    assert [row["id"] for row in kept] == ["keep"]
    assert meta["barrier_contract_included"] == 1
    assert meta["barrier_contract_mode_mismatch"] == 1
    # ATR_DYNAMIC is selected by contract version, not a fixed TP equality.
    assert meta["barrier_contract_tp_mismatch"] == 0
    assert meta["barrier_contract_missing"] == 1
    assert meta["barrier_contract_atr_non_v2_excluded"] == 1


def test_catboost_trial_selection_optimizes_validation_net_ev():
    predictions = np.array([0.9, 0.8, 0.2, 0.1])
    labels = np.array([1, 0, 1, 0])
    returns = np.array([1.0, -0.2, 0.1, -1.0])

    score = _validation_selection_score(
        predictions,
        labels,
        returns,
        grid_step=0.1,
        min_positives=1,
    )

    assert score == 1.0
