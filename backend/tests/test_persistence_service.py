from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import health_check_persistence  # noqa: E402
from app.services.persistence.jobs import PersistenceJob  # noqa: E402
from app.services.persistence.service import PersistenceService  # noqa: E402


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _factory():
    return _FakeSession()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_persistence_service_processes_jobs_and_updates_snapshot() -> None:
    service = PersistenceService(
        _factory,
        workers=2,
        queue_maxsize=4,
        service_name="test-persistence",
    )
    seen: list[str] = []
    service._repo.persist_job = AsyncMock(side_effect=lambda session, job: seen.append(job.key))

    await service.start()
    try:
        await service.enqueue(PersistenceJob(domain="unit", symbol="BTC_USDT"))
        await service.enqueue(PersistenceJob(domain="unit", symbol="ETH_USDT"))
        await service.join()
        snapshot = service.snapshot()
    finally:
        await service.stop()

    assert len(seen) == 2
    assert snapshot["queue"]["total_enqueued"] == 2
    assert snapshot["queue"]["total_processed"] == 2
    assert snapshot["workers"]["configured"] == 2
    assert snapshot["status"] == "ok"


@pytest.mark.anyio
async def test_persistence_service_retries_transient_failures() -> None:
    service = PersistenceService(
        _factory,
        workers=1,
        queue_maxsize=2,
        service_name="retry-persistence",
    )
    calls = 0

    async def _persist(session, job):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("deadlock detected")
        return None

    service._repo.persist_job = AsyncMock(side_effect=_persist)

    await service.start()
    try:
        await service.enqueue(PersistenceJob(domain="retry", symbol="SOL_USDT"))
        await service.join()
        snapshot = service.snapshot()
    finally:
        await service.stop()

    assert calls == 2
    assert snapshot["db"]["retry_count"] == 1
    assert snapshot["queue"]["total_failed"] == 0


def test_health_check_persistence_returns_503_when_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run():
        monkeypatch.setattr(
            "app.services.persistence.get_persistence_snapshot",
            lambda: {"status": "degraded", "queue": {"size": 1000}},
        )
        return await health_check_persistence()

    response = asyncio.run(_run())
    assert getattr(response, "status_code", 200) == 503
