import asyncio
import inspect
from datetime import datetime, timezone
from uuid import UUID, uuid4

from backend.app.tasks import opportunity_snapshot_evaluator as evaluator


def test_approved_profile_filter_uses_asyncpg_safe_uuid_array_cast():
    source = inspect.getsource(evaluator._evaluate_approved)

    assert ":pids::uuid[]" not in source
    assert "profile_id = ANY(CAST(:pids AS uuid[]))" in source


def test_approved_profile_filter_binds_uuid_collection():
    profile_id = uuid4()
    captured = {}

    class Result:
        def fetchone(self):
            return None

    class DB:
        async def execute(self, statement, params):
            captured.update(params)
            return Result()

    snap = {
        "id": uuid4(),
        "user_id": uuid4(),
        "symbol": "BTC_USDT",
        "created_at": datetime.now(timezone.utc),
        "profiles_approved": [str(profile_id)],
    }

    asyncio.run(evaluator._evaluate_approved(DB(), snap))

    assert captured["pids"] == [profile_id]
    assert all(isinstance(item, UUID) for item in captured["pids"])


def test_evaluator_uses_celery_nullpool_session_factory():
    source = inspect.getsource(evaluator._run_evaluator)

    assert "CeleryAsyncSessionLocal as AsyncSessionLocal" in source
    assert "from ..database import AsyncSessionLocal" not in source
