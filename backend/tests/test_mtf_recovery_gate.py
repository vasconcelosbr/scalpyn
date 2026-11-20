"""Exercise the actual recovery middleware without starting application jobs."""
import ast
from pathlib import Path
import re
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest


def recovery_app():
    # Isolate exactly the declarations from main; importing the full app can
    # initialize unrelated clients and schedulers on developer machines.
    source = ast.parse((Path(__file__).parents[1]/'app/main.py').read_text(encoding='utf-8'))
    names = {'_RECOVERY_RESOURCE_ID','_RECOVERY_EDIT_ROUTES','_RecoveryObservationGate'}
    nodes = [n for n in source.body if isinstance(n,ast.ClassDef) and n.name in names or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets)]
    scope = {'_re':re,'_BaseHTTPMiddleware':BaseHTTPMiddleware,'_JSONResponse':JSONResponse}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<recovery-gate>','exec'),scope)
    app = FastAPI()
    app.add_middleware(scope['_RecoveryObservationGate'])
    return app


def test_recovery_allows_only_observational_mtf_settings():
    app = recovery_app()
    @app.api_route('/api/{path:path}',methods=['PUT','POST','DELETE'])
    def endpoint(path:str): return {'reached':True}
    with TestClient(app) as client:
        for path in ('config','preferences'):
            assert client.put('/api/mtf/'+path).status_code == 200
            assert client.post('/api/mtf/'+path).status_code == 403
            assert client.delete('/api/mtf/'+path).status_code == 403
        for path in ('/api/orders/','/api/mtf/execute','/api/config/risk','/api/mtf/config/extra'):
            assert client.put(path).status_code == 403
            assert client.post(path).status_code == 403


def test_recovery_cmc_routes_keep_auth_and_user_scoped_storage(monkeypatch):
    from app.api import ai_keys as api
    from app.services import ai_keys_service as service

    app = recovery_app()
    app.include_router(api.router)
    db = object()
    uid = uuid4()
    app.dependency_overrides[api.get_db] = lambda: db
    save = AsyncMock(return_value={'key_hint': 'masked'})
    delete = AsyncMock(return_value=True)
    decrypt = AsyncMock(return_value=None)
    monkeypatch.setattr(service, 'save_ai_key', save)
    monkeypatch.setattr(service, 'delete_ai_key', delete)
    monkeypatch.setattr(service, 'get_decrypted_api_key', decrypt)
    path = '/api/ai-keys/coinmarketcap'
    with TestClient(app) as client:
        for method, route in [('POST', path), ('POST', path+'/test'), ('DELETE', path)]:
            assert client.request(method, route, json={}).status_code in (401, 403)
            response = client.request(method, route, json={}, headers={'Authorization': 'Bearer invalid'})
            assert response.status_code == 401
        save.assert_not_awaited()
        delete.assert_not_awaited()
        decrypt.assert_not_awaited()
        app.dependency_overrides[api.get_current_user_id] = lambda: uid
        assert client.post(path, json={'api_key': 'short'}).status_code == 422
        save.assert_not_awaited()
        fake_key = 'fixture-key-not-a-real-credential'
        response = client.post(path, json={'api_key': fake_key})
        assert response.status_code == 200
        assert response.json() == {'status': 'saved', 'provider': 'coinmarketcap', 'key_hint': 'masked'}
        assert fake_key not in response.text
        save.assert_awaited_once_with(db, uid, 'coinmarketcap', fake_key, None, None, None, None)
        # Missing-key response proves the read-only test handler was reached.
        response = client.post(path+'/test')
        assert response.status_code == 404
        assert response.json()['detail'] == 'CoinMarketCap key not configured.'
        decrypt.assert_awaited_once_with(db, uid, 'coinmarketcap')
        assert client.delete(path).status_code == 200
        delete.assert_awaited_once_with(db, uid, 'coinmarketcap')


@pytest.mark.parametrize('method,path', [
    ('POST', '/api/ai-keys/openai'),
    ('POST', '/api/ai-keys/radar/test'),
    ('DELETE', '/api/ai-keys/anthropic'),
    ('POST', '/api/ai-keys/coinmarketcap/extra'),
    ('POST', '/api/ai-keys/coinmarketcap/test/extra'),
    ('POST', '/api/ai-keys/coinmarketcap-other'),
    ('DELETE', '/api/ai-keys/coinmarketcap/test'),
    ('PUT', '/api/ai-keys/coinmarketcap'),
    ('PATCH', '/api/ai-keys/coinmarketcap'),
    ('POST', '/api/exchange-keys'),
    ('POST', '/api/orders/'),
    ('POST', '/api/mtf/execute'),
    ('PUT', '/api/config/risk'),
])
def test_recovery_cmc_exception_never_opens_other_mutations(method, path):
    app = recovery_app()
    @app.api_route('/api/{path:path}', methods=['POST', 'PUT', 'PATCH', 'DELETE'])
    def endpoint(path: str):
        raise AssertionError('Blocked mutation reached its handler')
    with TestClient(app) as client:
        response = client.request(method, path, json={})
        assert response.status_code == 403
        assert response.json() == {'detail': 'Recovery observation mode'}
