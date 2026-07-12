from datetime import datetime, timedelta, timezone
import math
import pytest

from app.ml.feature_contract_v2 import snapshot_hash
from app.ml.native_capture_governance import (
    LANE_CONTRACT, approval_guard_reason, lineage_errors, official_row_errors,
)

def valid_row():
    now=datetime.now(timezone.utc); snap={"b": [True, None], "a": "ação"}
    return {"source":"L3_LAB","profile_id":"p","features_snapshot":snap,"features_captured_at":now,"feature_hash":snapshot_hash(snap),"feature_extractor_version":"feature-engine-v2","feature_schema_version":"entry_features_v2","capture_contract_version":"point-in-time-v1","eligible_for_training":True}

def test_official_dataset_fails_closed_without_frontier():
    assert official_row_errors(valid_row(), None) == ["data_collection_not_started"]

def test_valid_native_row_and_hash():
    row=valid_row(); assert official_row_errors(row,row["features_captured_at"]-timedelta(seconds=1)) == []

def test_hash_is_canonical_and_rejects_nonfinite():
    assert snapshot_hash({"b":1,"a":2}) == snapshot_hash({"a":2,"b":1})
    with pytest.raises(ValueError): snapshot_hash({"x": math.nan})
    with pytest.raises(ValueError): snapshot_hash({"x": math.inf})

def test_lineage_and_lane_contracts_are_source_aware():
    assert lineage_errors({"source":"L3_LAB"}) == ["missing_profile_id"]
    assert LANE_CONTRACT["XGBOOST"]["sources"] == {"L1_SPECTRUM"}
    assert "L1_SPECTRUM" not in LANE_CONTRACT["LIGHTGBM"]["sources"]

@pytest.mark.parametrize("metrics,reason", [({"unproven_temporality_rows":1},"UNPROVEN_TEMPORALITY_PRESENT"),({"hash_mismatch":1},"SNAPSHOT_HASH_MISMATCH"),({"proven_rows":4},"INSUFFICIENT_PROVEN_TEMPORAL_DATA")])
def test_registry_approval_is_fail_closed(metrics, reason):
    assert approval_guard_reason(metrics,5) == reason
