"""Regression tests for Redis clients used by repeated Celery asyncio.run calls."""

from __future__ import annotations

import asyncio
from uuid import uuid4


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
    assert first.closed is False
    assert len(created) == 2

    # Avoid leaving the second loop-owned fake in module state for other tests.
    redis_client._async_client = None
    redis_client._async_client_loop = None


def test_config_service_uses_loop_aware_redis_client(monkeypatch):
    import redis.asyncio as aioredis

    from app.services import redis_client
    from app.services.config_service import ConfigService

    created = []

    class LoopBoundClient:
        def __init__(self):
            self.loop = asyncio.get_running_loop()

        async def get(self, key):
            assert asyncio.get_running_loop() is self.loop
            return b'{"source":"cache"}'

    def fake_from_url(*args, **kwargs):
        client = LoopBoundClient()
        created.append(client)
        return client

    monkeypatch.setattr(aioredis, "from_url", fake_from_url)
    monkeypatch.setattr(redis_client, "_async_client", None)
    monkeypatch.setattr(redis_client, "_async_client_loop", None)
    monkeypatch.setattr(redis_client, "_init_retry_after", 0.0)

    service = ConfigService()
    user_id = uuid4()

    async def read_cached_config():
        return await service.get_config(None, "score", user_id)

    assert asyncio.run(read_cached_config()) == {"source": "cache"}
    assert asyncio.run(read_cached_config()) == {"source": "cache"}
    assert len(created) == 2

    redis_client._async_client = None
    redis_client._async_client_loop = None
