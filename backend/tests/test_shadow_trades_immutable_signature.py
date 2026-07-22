from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from uuid import UUID

from backend.scripts.audit_shadow_trades_immutable_signature import (
    IMMUTABLE_COLUMNS,
    LIFECYCLE_COLUMNS,
    canonical_row,
    canonical_value,
    differing_columns,
)


def test_canonical_value_normalizes_json_null_numeric_uuid_and_timezone():
    assert canonical_value(None) is None
    assert canonical_value(Decimal("1.2300")) == "1.23"
    assert canonical_value(UUID("00000000-0000-0000-0000-000000000001")) == "00000000-0000-0000-0000-000000000001"
    assert canonical_value(datetime(2026, 7, 21, 20, 51, 37, tzinfo=timezone.utc)) == "2026-07-21T20:51:37.000000Z"
    assert list(canonical_value({"z": 1, "a": None})) == ["a", "z"]


def test_canonical_signature_is_stable_across_mapping_order():
    left = {"id": "1", "features_snapshot": {"b": 2, "a": 1}}
    right = {"features_snapshot": {"a": 1, "b": 2}, "id": "1"}
    assert hashlib.sha256(canonical_row(left, ("id", "features_snapshot"))).hexdigest() == hashlib.sha256(canonical_row(right, ("id", "features_snapshot"))).hexdigest()


def test_immutable_and_lifecycle_columns_are_disjoint_and_cover_primary_key():
    assert "id" in IMMUTABLE_COLUMNS
    assert set(IMMUTABLE_COLUMNS).isdisjoint(LIFECYCLE_COLUMNS)


def test_diff_classification_does_not_treat_normal_close_as_immutable():
    before = {"id": "1", "symbol": "BTC_USDT", "status": "RUNNING", "outcome": None}
    after = {"id": "1", "symbol": "BTC_USDT", "status": "COMPLETED", "outcome": "TP_HIT"}
    assert differing_columns(before, after, ("id", "symbol")) == []
    assert differing_columns(before, after, ("status", "outcome")) == ["status", "outcome"]


def test_native_snapshot_change_is_immutable_difference():
    before = {"features_snapshot": {"rsi": 50}, "feature_hash": "a"}
    after = {"features_snapshot": {"rsi": 51}, "feature_hash": "b"}
    assert differing_columns(before, after, ("features_snapshot", "feature_hash")) == ["features_snapshot", "feature_hash"]
