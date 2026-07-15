from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.profile_intelligence import _get_indicator_stats


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, latest_run_id, rows=None):
        self.latest_run_id = latest_run_id
        self.rows = rows or []
        self.scalar_statements = []
        self.execute_statements = []

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        return self.latest_run_id

    async def execute(self, statement):
        self.execute_statements.append(statement)
        return _ScalarRows(self.rows)


@pytest.mark.asyncio
async def test_indicator_stats_default_to_latest_completed_run():
    user_id = uuid4()
    latest_run_id = uuid4()
    db = _FakeDb(latest_run_id)

    result = await _get_indicator_stats(
        db, user_id, "winning_indicator", None, 10, None, None, 20
    )

    assert result == {
        "indicators": [],
        "role": "winning_indicator",
        "run_id": str(latest_run_id),
        "dataset_version": "pi-native-point-in-time-v1",
        "label_version": "shadow_outcome-v1",
    }
    assert len(db.scalar_statements) == 1
    assert len(db.execute_statements) == 1
    params = db.execute_statements[0].compile().params
    assert latest_run_id in params.values()
    compiled = str(db.execute_statements[0].compile())
    assert "avg_pnl_pct >" in compiled


@pytest.mark.asyncio
async def test_indicator_stats_keep_explicit_run_without_latest_lookup():
    user_id = uuid4()
    requested_run_id = uuid4()
    db = _FakeDb(uuid4())

    result = await _get_indicator_stats(
        db, user_id, "losing_indicator", str(requested_run_id), 10, None, None, 20
    )

    assert result["run_id"] == str(requested_run_id)
    assert db.scalar_statements == []
    params = db.execute_statements[0].compile().params
    assert requested_run_id in params.values()
    compiled = str(db.execute_statements[0].compile())
    assert "loss_rate DESC" in compiled
    assert "lift_vs_base ASC" in compiled
    assert "avg_pnl_pct <" in compiled


@pytest.mark.asyncio
async def test_indicator_stats_reject_invalid_run_id():
    db = _FakeDb(uuid4())

    with pytest.raises(HTTPException) as exc:
        await _get_indicator_stats(
            db, uuid4(), "winning_indicator", "invalid", 10, None, None, 20
        )

    assert exc.value.status_code == 400
    assert db.execute_statements == []
