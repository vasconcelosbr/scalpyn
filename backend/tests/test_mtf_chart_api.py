from unittest.mock import AsyncMock
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app.api import mtf_chart
from app.schemas.mtf_chart import MTFChartConfig, experimental_preset


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(mtf_chart.router)
    uid = uuid4()
    app.dependency_overrides[mtf_chart.get_current_user_id] = lambda: uid
    app.dependency_overrides[mtf_chart.get_db] = lambda: object()
    monkeypatch.setattr(mtf_chart.service, 'load_config', AsyncMock(return_value=MTFChartConfig.model_validate(experimental_preset())))
    monkeypatch.setattr(mtf_chart.service, 'members', AsyncMock(return_value=[{'symbol':'BTC_USDT','profile_id':None}]))
    monkeypatch.setattr(mtf_chart.service, 'latest_evaluations', AsyncMock(return_value={}))
    monkeypatch.setattr(mtf_chart.service, 'chart_data', AsyncMock(return_value={'mode':'SHADOW','operational_effect':False,
        'analysis':{'config_hash':'cfg','engine_version':'mtf_chart_v1'},'frames':{},'as_of':'now'}))
    write = AsyncMock(side_effect=AssertionError('GET attempted a config mutation'))
    monkeypatch.setattr(mtf_chart.config_service, 'update_config', write)
    return TestClient(app), uid, write


def test_get_snapshot_and_chart_read_only(client):
    http, uid, write = client
    assert http.get('/api/mtf/watchlist', params={'watchlist_id':'l3-consolidated'}).status_code == 200
    response = http.get('/api/mtf/chart', params={'watchlist_id':'l3-consolidated','symbol':'BTC_USDT'})
    assert response.json()['operational_effect'] is False
    assert mtf_chart.service.members.await_args.args[1] == uid
    write.assert_not_awaited()


def test_removed_or_foreign_asset_is_not_fetched(client):
    http, _, _ = client
    result = http.get('/api/mtf/chart', params={'watchlist_id':'l3-consolidated','symbol':'OTHER_USDT'})
    assert result.status_code == 404
    mtf_chart.service.chart_data.assert_not_awaited()


def test_cursor_never_bypasses_removed_membership(client):
    http, _, _ = client
    params = {'watchlist_id':'l3-consolidated','symbol':'BTC_USDT'}
    first = http.get('/api/mtf/chart', params=params).json()
    mtf_chart.service.members.return_value = []
    mtf_chart.service.chart_data.reset_mock()
    response = http.get('/api/mtf/chart', params={**params,'since':first['cursor']})
    assert response.status_code == 404
    mtf_chart.service.chart_data.assert_not_awaited()


def test_membership_expiring_during_cold_calculation_is_rejected(client):
    http, _, _ = client
    mtf_chart.service.members.side_effect = [[{'symbol':'BTC_USDT','profile_id':None}], []]
    assert http.get('/api/mtf/chart', params={'watchlist_id':'l3-consolidated','symbol':'BTC_USDT'}).status_code == 404


def test_missing_config_is_fail_closed(client, monkeypatch):
    http, _, _ = client
    monkeypatch.setattr(mtf_chart.service, 'load_config', AsyncMock(side_effect=ValueError('missing')))
    assert http.get('/api/mtf/watchlist', params={'watchlist_id':'l3-consolidated'}).status_code == 422
    mtf_chart.service.members.assert_not_awaited()


def test_required_config_and_foreign_watchlist_rejected(client, monkeypatch):
    http, _, write = client
    assert http.put('/api/mtf/config', json={'enabled':True}).status_code == 422
    monkeypatch.setattr(mtf_chart.service, 'watchlist_options', AsyncMock(return_value=[{'id':'l3-consolidated'}]))
    config = experimental_preset()
    config['monitored_watchlists'] = [str(uuid4())]
    assert http.put('/api/mtf/config', json=config).status_code == 422
    write.assert_not_awaited()


def test_auth_required_without_overrides():
    app = FastAPI()
    app.include_router(mtf_chart.router)
    assert TestClient(app).get('/api/mtf/bootstrap').status_code in (401,403)
