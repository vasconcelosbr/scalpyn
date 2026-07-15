import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.ml.feature_extractor import (
    MLLeakageError,
    assert_no_operational_feature_leakage,
    build_training_dataframe,
    extract_features,
)


def _record_with_snapshot(snapshot):
    return {
        "symbol": "BTC_USDT",
        "source": "L1_SPECTRUM",
        "created_at": "2026-07-08T00:00:00Z",
        "features_snapshot": snapshot,
        "pnl_pct": 1.0,
        "net_return_pct": 0.8,
        "holding_seconds": 120,
        "outcome": "TP_HIT",
    }


def test_crypto_ev_prefix_is_forbidden_in_feature_columns():
    with pytest.raises(MLLeakageError, match="crypto_ev_score"):
        assert_no_operational_feature_leakage(["rsi", "crypto_ev_score"])


def test_post_model_operational_prefix_is_forbidden_in_feature_columns():
    with pytest.raises(MLLeakageError, match="post_model_operational_rank"):
        assert_no_operational_feature_leakage(["adx", "post_model_operational_rank"])


def test_training_dataframe_fails_closed_when_snapshot_contains_crypto_ev():
    records = [_record_with_snapshot({"rsi": 55.0, "crypto_ev_score": 72.0})]

    with pytest.raises(MLLeakageError, match="crypto_ev_score"):
        build_training_dataframe(records)


def test_inference_feature_extraction_fails_closed_when_snapshot_contains_crypto_ev():
    with pytest.raises(MLLeakageError, match="crypto_ev_score"):
        extract_features({"rsi": 55.0, "crypto_ev_score": 72.0})
