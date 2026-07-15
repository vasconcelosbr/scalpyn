from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api import profile_intelligence_live as live_api
from app.services.profile_intelligence_autopilot_service import (
    ProfileIntelligenceAutopilotService,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _CaptureDb:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_live_indicator_performance_is_user_scoped_and_latest_run(monkeypatch):
    user_id = uuid4()
    run_id = uuid4()
    profile_id = uuid4()
    row = SimpleNamespace(
        profile_id=profile_id,
        profile_name="L3_PROFILE",
        indicator_name="adx",
        bucket="high",
        sample_count=42,
        win_count=24,
        loss_count=18,
        win_rate=0.5714,
        avg_pnl_pct=0.12,
        lift_vs_profile=0.08,
        run_id=run_id,
        created_at=None,
    )
    db = _CaptureDb([_Result([row]), _Result([])])
    monkeypatch.setattr(
        live_api,
        "load_pi_settings",
        AsyncMock(return_value={"min_closed_trades": 30}),
    )

    payload = await live_api.live_indicator_performance(
        limit=20,
        db=db,
        user_id=user_id,
    )

    assert payload["run_id"] == str(run_id)
    assert payload["minimum_cases"] == 30
    assert payload["top_winners"][0]["profile_name"] == "L3_PROFILE"
    assert all("p.user_id = CAST(:uid AS uuid)" in sql for sql, _ in db.calls)
    assert all("JOIN latest_run" in sql for sql, _ in db.calls)
    assert all(params["uid"] == str(user_id) for _, params in db.calls)


@pytest.mark.asyncio
async def test_indicator_adjustment_fails_closed_while_exploratory():
    service = ProfileIntelligenceAutopilotService()
    profile_id = uuid4()
    user_id = uuid4()
    stat = SimpleNamespace(
        validation_status="exploratory_only",
        actionability_status="exploratory_only",
    )
    profile = SimpleNamespace(id=profile_id, user_id=user_id, is_active=True)

    with pytest.raises(ValueError, match="indicator_not_actionable"):
        await service.create_candidate_from_indicator_stat(
            AsyncMock(),
            user_id=user_id,
            indicator_stat=stat,
            base_profile=profile,
        )


@pytest.mark.asyncio
async def test_indicator_adjustment_rejects_unassociated_profile():
    service = ProfileIntelligenceAutopilotService()
    profile_id = uuid4()
    user_id = uuid4()
    stat = SimpleNamespace(
        validation_status="validated",
        actionability_status="validated",
        role_detected="winning_indicator",
        avg_pnl_pct=0.1,
        source_profile_ids=[str(uuid4())],
        evidence_json={},
    )
    profile = SimpleNamespace(id=profile_id, user_id=user_id, is_active=True)

    with pytest.raises(ValueError, match="profile_not_associated_with_indicator"):
        await service.create_candidate_from_indicator_stat(
            AsyncMock(),
            user_id=user_id,
            indicator_stat=stat,
            base_profile=profile,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "avg_pnl_pct", "expected_error"),
    [
        ("winning_indicator", -0.01, "indicator_winner_has_non_positive_pnl"),
        ("losing_indicator", 0.01, "indicator_loser_has_non_negative_pnl"),
    ],
)
async def test_indicator_adjustment_rejects_wrong_economic_sign(
    role,
    avg_pnl_pct,
    expected_error,
):
    service = ProfileIntelligenceAutopilotService()
    profile_id = uuid4()
    user_id = uuid4()
    stat = SimpleNamespace(
        validation_status="validated",
        actionability_status="validated",
        role_detected=role,
        avg_pnl_pct=avg_pnl_pct,
    )
    profile = SimpleNamespace(id=profile_id, user_id=user_id, is_active=True)

    with pytest.raises(ValueError, match=expected_error):
        await service.create_candidate_from_indicator_stat(
            AsyncMock(),
            user_id=user_id,
            indicator_stat=stat,
            base_profile=profile,
        )
