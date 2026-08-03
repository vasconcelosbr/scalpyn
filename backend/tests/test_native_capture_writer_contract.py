from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.app.ml.feature_contract_v2 import (
    CAPTURE_CONTRACT_VERSION,
    FEATURE_EXTRACTOR_VERSION,
    FEATURE_SCHEMA_VERSION,
    REQUIRED_DIRECTIONAL_FEATURES,
    capture_native_snapshot,
    snapshot_hash,
)
from backend.app.models.shadow_trade import ShadowTrade
from backend.app.services.ml_challenger_service import MLChallengerService
from backend.app.services.shadow_trade_service import (
    _INSERT_SHADOW_SQL,
    _INSERT_STRATEGY_LAB_SQL,
    _get_current_price_multi_tf,
)


NATIVE_BINDINGS = {
    "event_id",
    "snapshot_id",
    "feature_source_at",
    "feature_source_times",
    "features_captured_at",
    "feature_hash",
    "feature_extractor_version",
    "feature_schema_version",
    "capture_contract_version",
    "lineage_status",
    "eligible_for_training",
}


def test_native_capture_generates_own_utc_timestamp(monkeypatch):
    native_time = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("backend.app.ml.feature_contract_v2.utcnow", lambda: native_time)

    source_time = native_time - timedelta(minutes=1)
    capture = capture_native_snapshot(
        {"rsi": 55.0, "atr_pct": 1.0},
        source_snapshot={
            "rsi": {"ts": source_time.isoformat()},
            "atr_pct": {"ts": source_time.isoformat()},
        },
    )

    assert capture.captured_at == native_time
    assert capture.source_at == source_time
    assert capture.source_times == {
        "atr_pct": source_time.isoformat(),
        "rsi": source_time.isoformat(),
    }
    assert capture.feature_extractor_version == FEATURE_EXTRACTOR_VERSION
    assert capture.feature_schema_version == FEATURE_SCHEMA_VERSION
    assert capture.capture_contract_version == CAPTURE_CONTRACT_VERSION


def test_hash_is_stable_for_key_order():
    assert snapshot_hash({"b": 2, "a": 1}) == snapshot_hash({"a": 1, "b": 2})


def test_deferred_capture_is_causal_when_source_precedes_decision():
    decision_at = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    source_at = decision_at - timedelta(minutes=1)
    snapshot = {
        "atr_pct": 1.0,
        **{name: None for name in REQUIRED_DIRECTIONAL_FEATURES},
    }
    capture = capture_native_snapshot(
        snapshot,
        source_snapshot={"atr_pct": {"ts": source_at.isoformat()}},
        decision_created_at=decision_at,
        entry_at=decision_at,
        captured_at=decision_at + timedelta(seconds=5),
    )
    assert capture.errors == ()
    assert capture.source_at == source_at


def test_source_timestamp_follows_canonical_alias_mapping():
    source_at = datetime(2026, 7, 12, 11, 59, tzinfo=timezone.utc)
    snapshot = {
        "atr_percent": 1.0,
        **{name: None for name in REQUIRED_DIRECTIONAL_FEATURES},
    }
    capture = capture_native_snapshot(
        snapshot,
        source_snapshot={
            "atr_percent": {"ts": source_at.isoformat()}
        },
        decision_created_at=source_at + timedelta(minutes=1),
        entry_at=source_at + timedelta(minutes=1),
    )
    assert capture.source_at == source_at
    assert not any(
        reason.startswith("missing_source_timestamp")
        for reason in capture.errors
    )


@pytest.mark.asyncio
async def test_entry_price_lookup_is_bounded_by_decision_time():
    decision_at = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

    class Result:
        def fetchone(self):
            return None

    class DB:
        statement = ""
        params = None

        async def execute(self, statement, params):
            self.statement = str(statement)
            self.params = params
            return Result()

    db = DB()
    assert await _get_current_price_multi_tf(
        db, "BTC_USDT", as_of=decision_at
    ) == (None, None)
    assert "time <= CAST(:as_of AS timestamptz)" in db.statement
    assert db.params["as_of"] == decision_at


def test_shadow_insert_paths_require_native_bindings():
    assert NATIVE_BINDINGS <= set(_INSERT_SHADOW_SQL._bindparams)
    assert NATIVE_BINDINGS <= set(_INSERT_STRATEGY_LAB_SQL._bindparams)


def test_shadow_model_exposes_native_contract_columns():
    for column_name in NATIVE_BINDINGS:
        assert hasattr(ShadowTrade, column_name)


@pytest.mark.asyncio
async def test_l1_loader_reads_only_official_native_rows():
    class Result:
        def fetchall(self):
            return []

    class DB:
        statement = ""

        async def execute(self, statement, params):
            self.statement = str(statement)
            return Result()

    db = DB()
    await MLChallengerService()._load_shadow_data(
        db,
        uuid4(),
        lookback_days=60,
        source_filter=["L1_SPECTRUM"],
        dataset_valid_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
        dataset_query_cutoff=datetime(2026, 7, 12, tzinfo=timezone.utc),
        maturity_embargo_margin_minutes=60,
    )

    assert "capture_contract_version = :capture_contract_version" in db.statement
    assert "feature_source_at IS NOT NULL" in db.statement
    assert "feature_source_at <= entry_timestamp" in db.statement
    assert "eligible_for_training IS TRUE" in db.statement
    assert "lineage_status = 'EXACT'" in db.statement
