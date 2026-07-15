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


def _load(**kwargs):
    service = MLChallengerService()
    session = _CapturingSession()

    async def run():
        await service._load_shadow_data(
            session,
            UUID("00000000-0000-0000-0000-000000000001"),
            30,
            dataset_query_cutoff=datetime(2026, 7, 12, tzinfo=timezone.utc),
            maturity_embargo_margin_minutes=60,
            # Fase 1 B.2 — fronteira temporal obrigatória em todo caminho de
            # montagem de dataset (fail-closed sem ela).
            dataset_valid_from=datetime(2026, 7, 11, tzinfo=timezone.utc),
            **kwargs,
        )

    asyncio.run(run())
    return session


def test_loader_requires_explicit_dataset_cutoff():
    async def run():
        with pytest.raises(ValueError, match="missing_dataset_query_cutoff"):
            await MLChallengerService()._load_shadow_data(
                _CapturingSession(),
                UUID("00000000-0000-0000-0000-000000000001"),
                30,
                maturity_embargo_margin_minutes=60,
            )

    asyncio.run(run())


def test_loader_requires_explicit_maturity_margin():
    async def run():
        with pytest.raises(ValueError, match="missing_ml_maturity_embargo_margin_minutes"):
            await MLChallengerService()._load_shadow_data(
                _CapturingSession(),
                UUID("00000000-0000-0000-0000-000000000001"),
                30,
                dataset_query_cutoff=datetime(2026, 7, 12, tzinfo=timezone.utc),
            )

    asyncio.run(run())


def test_loader_rejects_negative_margin_and_naive_cutoff():
    async def negative():
        with pytest.raises(ValueError, match="invalid_ml_maturity_embargo_margin_minutes"):
            await MLChallengerService()._load_shadow_data(
                _CapturingSession(),
                UUID("00000000-0000-0000-0000-000000000001"),
                30,
                dataset_query_cutoff=datetime(2026, 7, 12, tzinfo=timezone.utc),
                maturity_embargo_margin_minutes=-1,
            )

    async def naive():
        with pytest.raises(ValueError, match="invalid_dataset_query_cutoff_timezone"):
            await MLChallengerService()._load_shadow_data(
                _CapturingSession(),
                UUID("00000000-0000-0000-0000-000000000001"),
                30,
                dataset_query_cutoff=datetime(2026, 7, 12),
                maturity_embargo_margin_minutes=60,
            )

    asyncio.run(negative())
    asyncio.run(naive())


def test_loader_enforces_official_contract_resolution_and_maturity():
    session = _load(source_filter=["L1_SPECTRUM"])
    assert "eligible_for_training IS TRUE" in session.statement
    assert "lineage_status = 'EXACT'" in session.statement
    assert "COALESCE(label_resolved_at, completed_at) <= :dataset_query_cutoff" in session.statement
    assert "COALESCE(ttt_timeout_minutes, 0)" in session.statement
    assert "+ :maturity_embargo_margin_minutes" in session.statement
    assert session.params["maturity_embargo_margin_minutes"] == 60
    # Fase 1 B.2 — a query restringe à população pós-fronteira.
    assert "entry_timestamp >= :valid_from" in session.statement
    assert session.params["valid_from"] == datetime(
        2026, 7, 11, tzinfo=timezone.utc
    )
