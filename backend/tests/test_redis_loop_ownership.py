import asyncio
import sys
from pathlib import Path

import pytest
import redis.asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import redis_client


class LoopBoundClient:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.closed = False

    async def ping(self):
        assert self.loop is asyncio.get_running_loop(), "transport belongs to another loop"
        assert not self.closed
        return True

    async def aclose(self):
        assert self.loop is asyncio.get_running_loop()
        self.closed = True


def test_clients_are_reused_only_within_their_own_loop(monkeypatch):
    monkeypatch.setattr(redis.asyncio, "from_url", lambda *a, **kw: LoopBoundClient())
    clients = []

    async def invocation():
        client = await redis_client.get_async_redis()
        assert await client.ping()
        assert await redis_client.get_async_redis() is client
        clients.append(client)

    # Regression: the old singleton returned the first loop's transport here.
    asyncio.run(invocation())
    asyncio.run(invocation())
    assert clients[0] is not clients[1]


@pytest.mark.parametrize("module", ["pipeline_scan", "compute_indicators", "collect_market_data"])
@pytest.mark.parametrize("fail", [False, True])
def test_celery_closes_redis_before_loop_shutdown(monkeypatch, module, fail):
    from importlib import import_module
    runner = import_module(f"app.tasks.{module}")._run_async
    monkeypatch.setattr(redis.asyncio, "from_url", lambda *a, **kw: LoopBoundClient())
    clients = []

    async def invocation():
        client = await redis_client.get_async_redis()
        clients.append(client)
        assert await client.ping()
        if fail:
            raise ValueError("task failed")
        return "done"

    for _ in range(2):
        if fail:
            with pytest.raises(ValueError, match="task failed"):
                runner(invocation())
        else:
            assert runner(invocation()) == "done"
    assert clients[0] is not clients[1]
    assert all(client.closed and client.loop.is_closed() for client in clients)


def test_reset_does_not_close_another_loops_client(monkeypatch):
    monkeypatch.setattr(redis.asyncio, "from_url", lambda *a, **kw: LoopBoundClient())
    first, second = asyncio.new_event_loop(), asyncio.new_event_loop()
    try:
        a = first.run_until_complete(redis_client.get_async_redis())
        b = second.run_until_complete(redis_client.get_async_redis())
        first.run_until_complete(redis_client.reset_async_redis())
        assert a.closed
        assert second.run_until_complete(b.ping())
        second.run_until_complete(redis_client.reset_async_redis())
    finally:
        first.close()
        second.close()
