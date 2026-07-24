import pandas as pd

from backend.app.ml.feature_extractor import build_training_dataframe
from backend.app.services.ml_challenger_service import _apply_feature_contract


def test_optional_missing_value_does_not_violate_numeric_range():
    frame = pd.DataFrame(
        [
            {"rsi": 50.0, "spread_pct": None},
            {"rsi": 50.0, "spread_pct": -0.01},
            {"rsi": 50.0, "spread_pct": 0.02},
        ]
    )
    contract = {
        "required": ["rsi"],
        "optional": ["spread_pct"],
    }

    filtered, rejected = _apply_feature_contract(
        frame,
        contract,
        {"rsi": {"gte": 0, "lte": 100}, "spread_pct": {"gte": 0}},
        lane_name="L3_PROFILE",
    )

    assert list(filtered.index) == [0, 2]
    assert rejected == 1


def test_min_row_coverage_rejects_sparse_shape_under_same_contract():
    frame = pd.DataFrame(
        [
            {"rsi": 50.0, "adx": 20.0, "spread_pct": None, "bb_width": None},
            {"rsi": 50.0, "adx": 20.0, "spread_pct": 0.02, "bb_width": 0.15},
        ]
    )
    contract = {
        "required": ["rsi", "adx"],
        "optional": ["spread_pct", "bb_width"],
        "min_row_coverage": 0.7,
    }

    filtered, rejected = _apply_feature_contract(
        frame,
        contract,
        {"rsi": {"gte": 0, "lte": 100}, "spread_pct": {"gte": 0}},
        lane_name="L3_PROFILE",
    )

    assert list(filtered.index) == [1]
    assert rejected == 1


def test_positive_net_return_objective_logs_its_actual_label(caplog):
    with caplog.at_level("INFO"):
        build_training_dataframe(
            [
                {
                    "pnl_pct": 0.3,
                    "net_return_pct": 0.1,
                    "holding_seconds": 100,
                    "outcome": "TIMEOUT",
                    "features_snapshot": {},
                }
            ],
            win_fast_threshold_s=14400,
            label_objective="positive_net_return",
        )

    assert "label_objective=positive_net_return" in caplog.text
    assert "label_version=positive_net_return_v1" in caplog.text
