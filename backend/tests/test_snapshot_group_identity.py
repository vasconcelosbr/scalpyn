from datetime import datetime, timezone

from app.services.ml_challenger_service import (
    SNAPSHOT_GROUPING_CONTRACT_VERSION,
    _snapshot_group_key,
)


def _record(**overrides):
    record = {
        "features_snapshot": {"rsi": 55.0},
        "symbol": "BTC_USDT",
        "timeframe": "5m",
        "exchange": "gate",
        "created_at": datetime(2026, 7, 12, 4, 0, 10, tzinfo=timezone.utc),
    }
    record.update(overrides)
    return record


def test_snapshot_id_has_highest_priority():
    left = _record(snapshot_id="snapshot-1", event_id="event-1")
    right = _record(snapshot_id="snapshot-1", event_id="event-2", symbol="ETH_USDT")
    assert _snapshot_group_key(left) == _snapshot_group_key(right) == "snapshot:snapshot-1"


def test_event_id_includes_market_context():
    base = _record(snapshot_id=None, event_id="cycle-1")
    assert _snapshot_group_key(base) == _snapshot_group_key(dict(base))
    assert _snapshot_group_key(base) != _snapshot_group_key({**base, "symbol": "ETH_USDT"})


def test_created_at_is_never_used_as_capture_fallback():
    first = _record(snapshot_id=None, event_id=None)
    later = _record(
        snapshot_id=None,
        event_id=None,
        created_at=datetime(2026, 7, 12, 4, 1, 10, tzinfo=timezone.utc),
    )
    assert _snapshot_group_key(first) == _snapshot_group_key(later)
    assert _snapshot_group_key(first).startswith("feature_only:")


def test_historical_fallback_groups_profiles_in_same_event_minute():
    captured_at = datetime(2026, 7, 12, 4, 0, 10, tzinfo=timezone.utc)
    first = _record(
        snapshot_id=None, event_id=None, profile_id="profile-1",
        features_captured_at=captured_at,
    )
    second = _record(
        snapshot_id=None,
        event_id=None,
        profile_id="profile-2",
        features_captured_at=captured_at,
        created_at=datetime(2026, 7, 12, 4, 0, 50, tzinfo=timezone.utc),
    )
    assert _snapshot_group_key(first) == _snapshot_group_key(second)


def test_grouping_contract_is_versioned():
    assert SNAPSHOT_GROUPING_CONTRACT_VERSION == "market_event_v1"


def test_feature_only_fallback_is_explicitly_diagnostic():
    key = _snapshot_group_key({"features_snapshot": {"rsi": 55.0}})
    assert key.startswith("feature_only:")
