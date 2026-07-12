from datetime import datetime, timezone

import pytest

from app.ml.feature_contract_v2 import (
    CAPTURE_CONTRACT_VERSION,
    FEATURE_EXTRACTOR_VERSION,
    FEATURE_SCHEMA_VERSION,
    capture_native_snapshot,
    snapshot_hash,
)


def test_native_capture_generates_own_utc_timestamp(monkeypatch):
    native_time = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.ml.feature_contract_v2.utcnow", lambda: native_time)
    capture = capture_native_snapshot({"rsi": 55.0, "atr_pct": 1.0})
    assert capture.captured_at == native_time
    assert capture.feature_extractor_version == FEATURE_EXTRACTOR_VERSION
    assert capture.feature_schema_version == FEATURE_SCHEMA_VERSION
    assert capture.capture_contract_version == CAPTURE_CONTRACT_VERSION


def test_hash_is_stable_for_key_order():
    assert snapshot_hash({"b": 2, "a": 1}) == snapshot_hash({"a": 1, "b": 2})


def test_hash_rejects_non_finite_numbers():
    with pytest.raises(ValueError):
        snapshot_hash({"rsi": float("nan")})
