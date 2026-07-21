import inspect

from backend.app.tasks import opportunity_snapshot_evaluator as evaluator


def test_approved_profile_filter_uses_asyncpg_safe_uuid_array_cast():
    source = inspect.getsource(evaluator._evaluate_approved)

    assert ":pids::uuid[]" not in source
    assert "profile_id = ANY(CAST(:pids AS uuid[]))" in source


def test_evaluator_uses_celery_nullpool_session_factory():
    source = inspect.getsource(evaluator._run_evaluator)

    assert "CeleryAsyncSessionLocal as AsyncSessionLocal" in source
    assert "from ..database import AsyncSessionLocal" not in source
