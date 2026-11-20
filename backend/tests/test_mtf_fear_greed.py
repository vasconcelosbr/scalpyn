import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.services import mtf_fear_greed as service
from app.schemas.mtf_chart import MTFFearGreedConfig, MTFChartConfig, experimental_preset, presentation_preset

AT = 1795186800
CFG = MTFFearGreedConfig(enabled=True, refresh_seconds=900, max_age_seconds=3600)


def data(value=9, classification='Extreme Fear', at=AT):
    return {'value': value, 'value_classification': classification,
            'update_time': datetime.fromtimestamp(at, timezone.utc).isoformat()}


@pytest.mark.parametrize('value', [0, 9, 100])
def test_source_values_preserved_including_zero(value):
    result = service.snapshot({'data': data(value), 'fetched_at': 'original'}, CFG, now=AT)
    assert result['value'] == value and result['classification'] == 'Extreme Fear'
    assert result['status'] == 'READY' and result['fetched_at'] == 'original'


@pytest.mark.parametrize('value', [True, -1, 101, float('nan'), float('inf'), '9', None])
def test_invalid_values_never_become_sentiment(value):
    with pytest.raises(ValueError): service.reading(data(value), AT)


def test_invalid_classification_and_time():
    for payload in [data(classification=v) for v in ('unknown', '', None, [], True)] + [data(at=AT+1), {**data(), 'update_time': '2026-11-20T01:00:00'}]:
        with pytest.raises(ValueError): service.reading(payload, AT)


@pytest.mark.parametrize('source,expected', [('Extreme fear','Extreme Fear'), ('Extreme greed','Extreme Greed'), ('FEAR','Fear'), ('neutral','Neutral'), ('greed','Greed')])
def test_live_classification_casing_preserves_source_value_and_time(source, expected):
    payload = data(value=8, classification=source)
    result = service.snapshot({'data': payload, 'fetched_at': 'original'}, CFG, now=AT)
    assert result['status'] == 'READY' and result['classification'] == expected
    assert result['value'] == payload['value'] and result['update_time'] == payload['update_time']
    assert result['fetched_at'] == 'original'


def test_age_uses_source_time_not_latest_fetch_and_settings_do_not_change_engine():
    entry = {'data': data(at=AT-3601), 'fetched_at': datetime.fromtimestamp(AT, timezone.utc).isoformat()}
    assert service.snapshot(entry, CFG, now=AT)['status'] == 'STALE'
    base = MTFChartConfig.model_validate(experimental_preset())
    changed = base.model_copy(deep=True)
    changed.presentation = type(base).model_validate({**base.model_dump(), 'presentation': presentation_preset()}).presentation
    assert base.engine_dump() == changed.engine_dump()
    with pytest.raises(ValidationError): MTFFearGreedConfig(enabled=True, refresh_seconds=3600, max_age_seconds=900)


class Lock:
    def __init__(self, lock): self.lock = lock
    async def acquire(self): await self.lock.acquire(); return True
    async def release(self): self.lock.release()


class Redis:
    def __init__(self): self.values = {}; self.locks = {}
    async def get(self, key): return self.values.get(key)
    async def set(self, key, value, **kwargs): self.values[key] = value
    def lock(self, key, **kwargs): return Lock(self.locks.setdefault(key, asyncio.Lock()))


@pytest.mark.asyncio
async def test_coalesced_user_cache_rotation_failure_and_original_time(monkeypatch):
    redis = Redis(); now = [AT]
    monkeypatch.setattr(service, 'get_async_redis', AsyncMock(return_value=redis))
    monkeypatch.setattr(service.time, 'time', lambda: now[0])
    async def fetch(key):
        await asyncio.sleep(0)
        return data(), None
    fetcher = AsyncMock(side_effect=fetch); monkeypatch.setattr(service, 'fetch_reading', fetcher)
    results = await asyncio.gather(*(service.get_snapshot('user-a', 'private-key', CFG) for _ in range(4)))
    assert all(r['status'] == 'READY' for r in results); assert fetcher.await_count == 1
    await service.get_snapshot('user-b', 'private-key', CFG)
    await service.get_snapshot('user-a', 'replacement-key', CFG)
    assert fetcher.await_count == 3
    assert not any('private-key' in k or 'replacement-key' in k for k in redis.values)
    now[0] += 901; fetcher.side_effect = None; fetcher.return_value = (None, 'RATE_LIMITED')
    result = await service.get_snapshot('user-a', 'private-key', CFG)
    assert result['status'] == 'STALE' and result['value'] == 9 and result['reason'] == 'RATE_LIMITED'
    assert result['update_time'] == results[0]['update_time'] and result['fetched_at'] == results[0]['fetched_at']
    await service.get_snapshot('user-a', 'private-key', CFG); assert fetcher.await_count == 4
    assert (await service.get_snapshot('user-a', None, CFG))['status'] == 'NOT_CONFIGURED'
    assert (await service.get_snapshot('user-a', 'private-key', None))['status'] == 'DISABLED'
    assert fetcher.await_count == 4


@pytest.mark.asyncio
async def test_cache_outage_does_not_fan_out_to_provider(monkeypatch):
    monkeypatch.setattr(service, 'get_async_redis', AsyncMock(return_value=None))
    fetch = AsyncMock(); monkeypatch.setattr(service, 'fetch_reading', fetch)
    assert (await service.get_snapshot('user', 'private-key', CFG))['reason'] == 'CACHE_UNAVAILABLE'
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('code,expected', [(200, None), (429, 'RATE_LIMITED'), (401, 'KEY_OR_PLAN_REJECTED'), (403, 'KEY_OR_PLAN_REJECTED')])
async def test_official_api_only_and_provider_errors(monkeypatch, code, expected):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            assert url == 'https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest'
            assert kwargs['headers']['X-CMC_PRO_API_KEY'] == 'private-key'
            return httpx.Response(code, json={'data':data(classification='Extreme fear'),'status':{'error_code':'0'}}, request=httpx.Request('GET',url))
    monkeypatch.setattr(service.httpx, 'AsyncClient', lambda **kwargs: Client())
    monkeypatch.setattr(service.time, 'time', lambda: AT)
    value, error = await service.fetch_reading('private-key')
    assert error == expected
    assert value == data() if code == 200 else value is None


def test_endpoint_auth_read_only_and_releases_transaction(monkeypatch):
    from app.api import mtf_chart as api
    from app.services import ai_keys_service
    app = FastAPI(); app.include_router(api.router)
    with TestClient(app) as client: assert client.get('/api/mtf/fear-greed').status_code in (401,403)
    uid = uuid4(); db = SimpleNamespace(rollback=AsyncMock())
    app.dependency_overrides[api.get_current_user_id] = lambda: uid
    app.dependency_overrides[api.get_db] = lambda: db
    monkeypatch.setattr(api, 'config_or_error', AsyncMock(return_value=SimpleNamespace(presentation=SimpleNamespace(fear_greed=CFG))))
    key = AsyncMock(return_value=None); monkeypatch.setattr(ai_keys_service, 'get_decrypted_api_key', key)
    async def snapshot(user_id, plain_key, cfg):
        db.rollback.assert_awaited_once(); assert user_id == uid and plain_key is None
        return {'status':'NOT_CONFIGURED'}
    monkeypatch.setattr(service, 'get_snapshot', snapshot)
    monkeypatch.setattr(api.service, 'evaluate', lambda *a,**k: pytest.fail('display evaluated strategy'))
    monkeypatch.setattr(api.config_service, 'update_config', AsyncMock(side_effect=AssertionError('display wrote config')))
    with TestClient(app) as client:
        response = client.get('/api/mtf/fear-greed')
        assert response.status_code == 200 and response.json()['status'] == 'NOT_CONFIGURED'
        assert response.headers['cache-control'] == 'private, no-store'
    key.assert_awaited_once_with(db, uid, 'coinmarketcap')
