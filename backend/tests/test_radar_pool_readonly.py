"""Pool export auth and no-write regression; synthetic rows only."""
from uuid import uuid4
import pytest
from test_radar_readonly import setup,headers,URL,POOL,OWNER,Result,NOW
POOL_URL=URL.replace('/assets','/pool-context')

@pytest.mark.parametrize('key',[None,'','wrong'])
def test_pool_key_required_before_database(setup,key):
    client,session=setup
    assert client.get(POOL_URL,headers=headers(key) if key is not None else {}).status_code==401
    assert not session.statements

@pytest.mark.parametrize('missing',[True,False])
def test_pool_owner_and_complete_rows(setup,monkeypatch,missing):
    client,session=setup;original=session.execute
    async def execute(statement,params=None):
        sql=str(statement)
        if 'FROM pools' in sql:
            assert 'user_id=:owner' in sql and params['owner']==OWNER
            return Result([] if missing else [dict(id=POOL,name='POOL',market_type='spot',is_active=True,updated_at=NOW)])
        if 'FROM pool_coins' in sql:
            assert 'LIMIT' not in sql and 'is_active=' not in sql
            return Result([dict(id=uuid4(),symbol=symbol,market_type='spot',is_active=active,is_approved=None,is_tradable=None,held_for_open_position=False,added_at=None,origin=None,discovered_at=None,radar_last_seen_at=None) for symbol,active in [('AKE_USDT',True),('INACTIVE_USDT',False)]])
        return await original(statement,params)
    monkeypatch.setattr(session,'execute',execute)
    r=client.get(POOL_URL,headers=headers())
    assert r.status_code==(404 if missing else 200)
    assert r.headers['cache-control']=='private, no-store'
    assert session.rollbacks==1
    if not missing:
        data=r.json();assert data['row_count']==2 and data['active_count']==1
        assert data['coins'][1]['indicator_readings']==[]
        assert data['coins'][0]['indicator_readings'][0]['values']['rsi']['value']==42
        assert data['watchlist']['contract_version']=='radar_watchlist_v1'

@pytest.mark.parametrize('method',['post','put','patch','delete'])
def test_pool_mutation_not_registered(setup,method):
    client,session=setup
    assert getattr(client,method)(POOL_URL,headers=headers()).status_code==405
    assert not session.statements
