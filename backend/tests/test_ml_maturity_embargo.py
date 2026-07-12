import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.services.ml_challenger_service import MLChallengerService


class _Result:
    def fetchall(self):
        return []


class _CapturingSession:
    def __init__(self):
        self.statement = None
        self.params = None

    async def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return _Result()


def test_loader_requires_explicit_maturity_margin():
    async def run():
        service = MLChallengerService()
        with pytest.raises(ValueError, match="missing_ml_maturity_embargo_margin_minutes"):
            await service._load_shadow_data(
                _CapturingSession(),
                UUID("00000000-0000-0000-0000-000000000001"),
                30,
                dataset_query_cutoff=datetime(2026, 7, 12, tzinfo=timezone.utc),
            )

    asyncio.run(run())


def test_loader_enforces_resolution_and_observation_maturity():
    async def run():
        service = MLChallengerService()
        session = _CapturingSession()
        cutoff = datetime(2026, 7, 12, 4, 26, tzinfo=timezone.utc)
        await service._load_shadow_data(
            session,
            UUID("00000000-0000-0000-0000-000000000001"),
            30,
            source_filter=["L3"],
            dataset_query_cutoff=cutoff,
            maturity_embargo_margin_minutes=60,
        )
        assert "COALESCE(label_resolved_at, completed_at) <= :dataset_query_cutoff" in session.statement
        assert "COALESCE(ttt_timeout_minutes, 0) + :maturity_embargo_margin_minutes" in session.statement
        assert session.params["dataset_query_cutoff"] == cutoff
        assert session.params["maturity_embargo_margin_minutes"] == 60

    asyncio.run(run())
