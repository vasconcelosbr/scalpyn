"""Recovery route access is paired with an atomic Shadow-only write boundary."""
import ast
from copy import deepcopy
from pathlib import Path
import re
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.api import strategy_settings as api
from test_strategy_settings_service import aggregate

@pytest.fixture
def recovery_client(aggregate, monkeypatch):
    uid, db = aggregate
    monkeypatch.setenv('RECOVERY_OBSERVATION_ONLY', '1')
    monkeypatch.setenv('RECOVERY_BLOCK_APP_EDITS', '1')
    invalidate = AsyncMock(return_value=True)
    monkeypatch.setattr('app.services.strategy_settings_service.config_service.invalidate_cache', invalidate)
    source = ast.parse((Path(__file__).parents[1] / 'app/main.py').read_text(encoding='utf-8'))
    names = {'_RECOVERY_RESOURCE_ID', '_RECOVERY_EDIT_ROUTES', '_RecoveryObservationGate'}
    nodes = [n for n in source.body if isinstance(n, ast.ClassDef) and n.name in names or (isinstance(n, ast.Assign) and any((isinstance(t, ast.Name) and t.id in names for t in n.targets)))]
    scope = {'_re': re, '_BaseHTTPMiddleware': BaseHTTPMiddleware, '_JSONResponse': JSONResponse}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<gate>', 'exec'), scope)
    app = FastAPI()
    app.include_router(api.router)
    app.add_middleware(scope['_RecoveryObservationGate'])
    app.dependency_overrides[api.get_db] = lambda: db
    app.dependency_overrides[api.get_current_user_id] = lambda: uid
    with TestClient(app) as client:
        yield (client, db, invalidate)

def test_recovery_form_saves_shadow_preserves_raw_spot_and_invalidates_cache(recovery_client):
    client, db, invalidate = recovery_client
    spot = next((p for p in db.profiles if p.config_type == 'spot_engine'))
    spot.config_json.pop('sell_flow')
    protected = {p.config_type: deepcopy(p.config_json) for p in db.profiles if p.config_type != 'ml'}
    current = client.get('/api/strategy-settings/config').json()['config']
    payload = deepcopy(current)
    payload['ml_shadow'].update({'shadow_barrier_mode': 'FIXED', 'ml_active_barrier_contract_version': 'shadow_fixed_v1', 'shadow_fixed_tp_pct': 1.0, 'shadow_fixed_sl_pct': 5.0, 'shadow_only_tp_sl': True})
    validated = client.post('/api/strategy-settings/import/validate', json={'payload': payload, 'source_hash': current['source_hash']})
    assert validated.status_code == 200, validated.text
    response = client.put('/api/strategy-settings/config', json={'payload': payload, 'source_hash': current['source_hash'], 'source': 'FORM'})
    assert response.status_code == 200, response.text
    assert response.json()['changed_config_types'] == ['ml']
    fresh = client.get('/api/strategy-settings/config').json()['config']
    assert fresh['ml_shadow']['shadow_fixed_tp_pct'] == 1.0
    assert fresh['ml_shadow']['shadow_fixed_sl_pct'] == 5.0
    assert fresh['source_hash'] != current['source_hash']
    assert {p.config_type: p.config_json for p in db.profiles if p.config_type != 'ml'} == protected
    assert next((p for p in db.profiles if p.config_type == 'ml')).config_json['unrelated_training_key'] == {'keep': True}
    invalidate.assert_awaited_once()
    assert invalidate.call_args.args[0] == 'ml' and invalidate.call_args.kwargs['strict'] is True
    assert db.commits == 1 and len(db.audits) == 1
    stale = client.put('/api/strategy-settings/config', json={'payload': payload, 'source_hash': current['source_hash'], 'source': 'FORM'})
    assert stale.status_code == 409 and db.commits == 1

@pytest.mark.parametrize('group', ['strategy', 'spot_engine'])
def test_recovery_rejects_mixed_live_edits_before_any_write(recovery_client, group):
    client, db, invalidate = recovery_client
    before = {p.config_type: deepcopy(p.config_json) for p in db.profiles}
    current = client.get('/api/strategy-settings/config').json()['config']
    payload = deepcopy(current)
    payload['ml_shadow']['shadow_atr_multiplier_tp'] = 2.0
    if group == 'strategy':
        payload[group]['strategies'][0]['enabled'] = False
    else:
        payload[group]['selling']['take_profit_pct'] = 2.0
    for method, path in [(client.put, '/api/strategy-settings/config'), (client.post, '/api/strategy-settings/import/validate')]:
        response = method(path, json={'payload': payload, 'source_hash': current['source_hash']})
        assert response.status_code == 403, response.text
        assert group in response.json()['detail']
    assert {p.config_type: p.config_json for p in db.profiles} == before
    assert db.commits == 0 and db.audits == []
    invalidate.assert_not_awaited()
    for method, path in [(client.post, '/api/orders/'), (client.put, '/api/config/ml'), (client.post, '/api/strategy-settings/multilayer-shadow/activate'), (client.put, '/api/strategy-settings/config/extra')]:
        assert method(path, json={}).status_code == 403
