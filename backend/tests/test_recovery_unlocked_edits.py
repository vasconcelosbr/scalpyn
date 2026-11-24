"""Application edits remain available while exchange execution is blocked."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_mtf_recovery_gate import recovery_app
from test_strategy_settings_service import aggregate


def unlocked_app(monkeypatch):
    monkeypatch.setenv('RECOVERY_OBSERVATION_ONLY', '1')
    monkeypatch.delenv('RECOVERY_BLOCK_APP_EDITS', raising=False)
    # Execute the actual production registration condition, not a test copy.
    legacy = recovery_app()
    gate = legacy.user_middleware[0].cls
    source = ast.parse((Path(__file__).parents[1] / 'app/main.py').read_text(encoding='utf-8'))
    nodes = [node for node in source.body if isinstance(node, ast.If)
             and '_RecoveryObservationGate' in ast.unparse(node)]
    assert len(nodes) == 1
    app = FastAPI()
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<registration>', 'exec'),
         {'app': app, '_os': os, '_RecoveryObservationGate': gate})
    return app


def test_exchange_save_keeps_auth_and_encrypts_credentials(monkeypatch):
    from app.api import exchanges as api
    app = unlocked_app(monkeypatch)
    app.include_router(api.router)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: None))),
        add=Mock(), commit=AsyncMock(), rollback=AsyncMock())
    app.dependency_overrides[api.get_db] = lambda: db
    monkeypatch.setattr(api, 'encrypt', lambda value: 'encrypted:' + value)
    uid = uuid4()
    with TestClient(app) as client:
        assert client.post('/api/exchanges/connect', json={}).status_code in (401, 403)
        db.commit.assert_not_awaited()
        app.dependency_overrides[api.get_current_user_id] = lambda: uid
        assert client.post('/api/exchanges/connect', json={}).status_code == 400
        response = client.post('/api/exchanges/connect', json={
            'exchange_name': 'Gate.io', 'api_key': 'fixture-key', 'api_secret': 'fixture-secret'})
        assert response.status_code == 200, response.text
        saved = db.add.call_args.args[0]
        assert saved.user_id == uid
        assert saved.api_key_encrypted == 'encrypted:fixture-key'
        assert saved.api_secret_encrypted == 'encrypted:fixture-secret'
        db.commit.assert_awaited_once()


@pytest.mark.parametrize('group', ['strategy', 'spot_engine'])
def test_strategy_edits_save_and_invalidate_cache_with_orders_blocked(aggregate, monkeypatch, group):
    from app.api import strategy_settings as api
    uid, db = aggregate
    app = unlocked_app(monkeypatch)
    app.include_router(api.router)
    app.dependency_overrides[api.get_db] = lambda: db
    app.dependency_overrides[api.get_current_user_id] = lambda: uid
    invalidate = AsyncMock(return_value=True)
    monkeypatch.setattr('app.services.strategy_settings_service.config_service.invalidate_cache', invalidate)
    with TestClient(app) as client:
        current = client.get('/api/strategy-settings/config').json()['config']
        payload = current.copy()
        if group == 'strategy':
            payload[group]['strategies'][0]['enabled'] = False
        else:
            payload[group]['selling']['take_profit_pct'] = 2.0
        response = client.put('/api/strategy-settings/config', json={
            'payload': payload, 'source_hash': current['source_hash'], 'source': 'FORM'})
        assert response.status_code == 200, response.text
        assert group in response.json()['changed_config_types']
        fresh = client.get('/api/strategy-settings/config').json()['config']
        assert fresh[group] == payload[group]
        assert db.commits == 1
        assert any(call.args[0] == group for call in invalidate.await_args_list)
        assert os.environ['RECOVERY_OBSERVATION_ONLY'] == '1'
