from datetime import datetime, timezone

import pytest

from app.services.l3_gate_evaluation_store import (
    _capture_row,
    persist_gate_evaluations,
)


def _decision(*, hash_value: str = "a" * 64, operational_effect: bool = False):
    gate = {
        "contract_version": "l3_gate_v2",
        "evaluation_envelope_hash": hash_value,
        "evaluated_at": datetime(2026, 8, 22, 23, 5, tzinfo=timezone.utc),
        "legacy_decision": "ALLOW",
        "shadow_decision": "BLOCK",
        "decision_drift": True,
        "operational_effect": operational_effect,
        "signals": {"gate_passed": False, "conditions": []},
        "entry_triggers": {"gate_passed": True, "conditions": []},
    }
    if operational_effect:
        gate.update(
            {
                "promotion_status": "OPERATIONAL",
                "operational_decision": "BLOCK",
            }
        )
    return {
        "symbol": "LIT_USDT",
        "timeframe": "5m",
        "gate_evaluation_v2": gate,
        "metrics": {"l3_gate_v2": gate},
    }


def test_capture_row_preserves_observational_contract():
    row = _capture_row(
        _decision(),
        user_id="00000000-0000-0000-0000-000000000001",
        watchlist_id="00000000-0000-0000-0000-000000000002",
        profile_id="00000000-0000-0000-0000-000000000003",
        profile_name="L3_RSI_COOLDOWN_RELOAD_V1",
    )

    assert row["evaluation_envelope_hash"] == "a" * 64
    assert row["decision_drift"] is True
    assert row["operational_effect"] is False
    assert '"operational_effect":false' in row["payload"]


def test_capture_row_preserves_governed_operational_contract():
    row = _capture_row(
        _decision(operational_effect=True),
        user_id=None,
        watchlist_id=None,
        profile_id=None,
        profile_name=None,
    )

    assert row["operational_effect"] is True
    assert '"operational_decision":"BLOCK"' in row["payload"]


def test_capture_row_preserves_profile_contract_deny():
    decision = _decision(operational_effect=True)
    decision["gate_evaluation_v2"]["promotion_status"] = "PROFILE_CONTRACT_DENY"

    row = _capture_row(
        decision,
        user_id=None,
        watchlist_id=None,
        profile_id=None,
        profile_name=None,
    )

    assert row["operational_effect"] is True
    assert '"promotion_status":"PROFILE_CONTRACT_DENY"' in row["payload"]


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (_decision(hash_value="bad"), "evaluation_envelope_hash_invalid"),
    ],
)
def test_capture_row_rejects_invalid_payload(decision, reason):
    with pytest.raises(ValueError, match=reason):
        _capture_row(
            decision,
            user_id=None,
            watchlist_id=None,
            profile_id=None,
            profile_name=None,
        )


def test_capture_row_rejects_incomplete_operational_metadata():
    decision = _decision()
    decision["gate_evaluation_v2"]["operational_effect"] = True

    with pytest.raises(ValueError, match="operational_promotion_metadata_invalid"):
        _capture_row(
            decision,
            user_id=None,
            watchlist_id=None,
            profile_id=None,
            profile_name=None,
        )


def test_capture_row_rejects_operational_label_without_effect():
    decision = _decision()
    decision["gate_evaluation_v2"]["promotion_status"] = "OPERATIONAL"

    with pytest.raises(ValueError, match="operational_promotion_metadata_invalid"):
        _capture_row(
            decision,
            user_id=None,
            watchlist_id=None,
            profile_id=None,
            profile_name=None,
        )


@pytest.mark.asyncio
async def test_persistence_is_idempotent_and_reports_replay():
    class Result:
        def __init__(self, attempts):
            self.attempts = attempts

        def fetchone(self):
            return ("evaluation-id", self.attempts)

    class FakeDb:
        def __init__(self):
            self.calls = 0

        async def execute(self, statement, params):
            self.calls += 1
            return Result(self.calls)

    db = FakeDb()
    kwargs = {
        "user_id": None,
        "watchlist_id": None,
        "profile_id": None,
        "profile_name": None,
    }
    first = await persist_gate_evaluations(db, [_decision()], **kwargs)
    replay = await persist_gate_evaluations(db, [_decision()], **kwargs)

    assert first == {
        "expected": 1,
        "captured": 1,
        "inserted": 1,
        "replayed": 0,
        "invalid": 0,
    }
    assert replay["captured"] == 1
    assert replay["inserted"] == 0
    assert replay["replayed"] == 1


@pytest.mark.asyncio
async def test_invalid_capture_is_visible_as_count_mismatch():
    class FakeDb:
        async def execute(self, statement, params):
            raise AssertionError("invalid payload must not reach the database")

    report = await persist_gate_evaluations(
        FakeDb(),
        [_decision(hash_value="bad")],
        user_id=None,
        watchlist_id=None,
        profile_id=None,
        profile_name=None,
    )

    assert report["expected"] == 1
    assert report["captured"] == 0
    assert report["invalid"] == 1
