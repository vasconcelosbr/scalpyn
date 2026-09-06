import pytest

from app.tasks import shadow_l3_continuation as task_module


@pytest.mark.asyncio
async def test_sweep_resets_async_redis_before_task_loop_closes(monkeypatch):
    events = []

    async def fake_sweep():
        events.append("sweep")
        return {"processed": 1, "errors": 0}

    async def fake_reset():
        events.append("reset")

    monkeypatch.setattr(task_module, "_sweep", fake_sweep)
    monkeypatch.setattr(
        "app.services.redis_client.reset_async_redis", fake_reset
    )

    result = await task_module._sweep_with_redis_cleanup()

    assert result == {"processed": 1, "errors": 0}
    assert events == ["sweep", "reset"]


@pytest.mark.asyncio
async def test_sweep_resets_async_redis_after_failure(monkeypatch):
    events = []

    async def fake_sweep():
        events.append("sweep")
        raise RuntimeError("boom")

    async def fake_reset():
        events.append("reset")

    monkeypatch.setattr(task_module, "_sweep", fake_sweep)
    monkeypatch.setattr(
        "app.services.redis_client.reset_async_redis", fake_reset
    )

    with pytest.raises(RuntimeError, match="boom"):
        await task_module._sweep_with_redis_cleanup()

    assert events == ["sweep", "reset"]
