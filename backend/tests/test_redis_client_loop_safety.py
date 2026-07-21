"""Regression tests for Redis clients used by repeated Celery asyncio.run calls."""

from __future__ import annotations

import asyncio


def test_get_async_redis_recreates_client_for_a_new_event_loop(monkeypatch):
    import redis.asyncio as aioredis

    from app.services import redis_client

    created = []

    class FakeClient:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    def fake_from_url(*args, **kwargs):
        client = FakeClient()
        created.append(client)
        return client

    monkeypatch.setattr(aioredis, "from_url", fake_from_url)
    monkeypatch.setattr(redis_client, "_async_client", None)
    monkeypatch.setattr(redis_client, "_async_client_loop", None)
    monkeypatch.setattr(redis_client, "_init_retry_after", 0.0)

    async def get_twice_in_one_loop():
        first = await redis_client.get_async_redis()
        second = await redis_client.get_async_redis()
        assert second is first
        return first

    first = asyncio.run(get_twice_in_one_loop())
    second = asyncio.run(redis_client.get_async_redis())

    assert second is not first
    assert first.closed is True
    assert len(created) == 2

    # Avoid leaving the second loop-owned fake in module state for other tests.
    redis_client._async_client = None
    redis_client._async_client_loop = None
