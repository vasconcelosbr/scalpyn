from datetime import datetime, timedelta, timezone

from app.ml.feature_contract_v2 import snapshot_hash
from app.ml.native_capture_governance import (
    ALERT_RULES,
    LANE_CONTRACT,
    approval_guard_reason,
    classify_native_row,
    official_row_errors,
)


def valid_row():
    now = datetime.now(timezone.utc)
    snapshot = {"atr_pct": 0.5, "signal": "bullish"}
    return {
        "source": "L3_LAB",
        "profile_id": "profile",
        "profile_version_id": "profile-version",
        "score_engine_version_id": "score-version",
        "lineage_status": "EXACT",
        "features_snapshot": snapshot,
        "features_captured_at": now,
        "feature_hash": snapshot_hash(snapshot),
        "feature_extractor_version": "feature-engine-v2",
        "feature_schema_version": "entry_features_v2",
        "capture_contract_version": "point-in-time-v1",
        "eligible_for_training": True,
    }


def test_official_dataset_fails_closed_without_frontier():
    assert official_row_errors(valid_row(), None) == ["data_collection_not_started"]


def test_valid_native_row_is_official_eligible():
    row = valid_row()
    start = row["features_captured_at"] - timedelta(seconds=1)
    assert classify_native_row(row, start) == ("official_native_eligible", [])


def test_ineligible_native_row_is_historical_quarantine():
    row = valid_row()
    row["eligible_for_training"] = False
    row["lineage_status"] = "INVALID_FEATURES"
    start = row["features_captured_at"] - timedelta(seconds=1)
    bucket, errors = classify_native_row(row, start)
    assert bucket == "historical_quarantine"
    assert "not_training_eligible" in errors
    assert "invalid_lineage_status" in errors


def test_eligible_row_with_bad_hash_is_official_invalid():
    row = valid_row()
    row["feature_hash"] = "bad"
    start = row["features_captured_at"] - timedelta(seconds=1)
    bucket, errors = classify_native_row(row, start)
    assert bucket == "official_invalid"
    assert errors == ["hash_mismatch"]


def test_reference_clock_controls_future_timestamp_check():
    row = valid_row()
    start = row["features_captured_at"] - timedelta(seconds=1)
    earlier = row["features_captured_at"] - timedelta(milliseconds=1)
    bucket, errors = classify_native_row(row, start, reference_now=earlier)
    assert bucket == "official_invalid"
    assert errors == ["future_timestamp"]


def test_lane_and_gate_contracts_are_fail_closed():
    assert LANE_CONTRACT["LIGHTGBM"]["sources"] == {"L1_SPECTRUM"}
    assert ALERT_RULES["hash_mismatch_gt_0"] == ("hash_mismatch", "gt", 0)
    assert approval_guard_reason({"official_invalid": 1}, 5) == "OFFICIAL_DATASET_INVALID"
    assert (
        approval_guard_reason({"official_native_eligible": 4}, 5)
        == "INSUFFICIENT_PROVEN_TEMPORAL_DATA"
    )
