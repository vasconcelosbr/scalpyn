"""External ARMED feed never changes profile membership or grants execution."""
import asyncio
import copy
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.services import l3_armed_observation as service
from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.services.profile_runtime_config import canonical_hash
from test_mtf_chart_postgres import database
from test_mtf_history import migrate, Adapter


NOW = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)


def context():
    uid, pid, cid = uuid4(), uuid4(), uuid4()
    cfg = MTFChartConfig.model_validate(experimental_preset())
    identity = dict(config_profile_id=str(cid), config_user_id=str(uid), config_hash=canonical_hash({'rsi':{}}))
    item = dict(symbol='BTC_USDT', profile_id=str(pid), status='approved', executable=False, execution_authority=False)
    analysis = dict(status='ARMED', profile_id=str(pid), profile_hash=canonical_hash({}),
                    config_hash=canonical_hash(cfg.engine_dump()), indicator_identity=identity,
                    engine_version=service.ENGINE_VERSION, candle_close_at_utc=NOW.isoformat())
    row = dict(id=uuid4(), symbol='BTC_USDT', profile_id=str(pid), observed_at=NOW,
               status='ARMED', quality='current', analysis=analysis)
    kwargs = dict(now=NOW, cfg=cfg, profile_hashes={str(pid):canonical_hash({})}, indicator_identity=identity)
    return uid, pid, cid, item, row, kwargs


@pytest.mark.parametrize('state', ['WAIT','ARMED','TRIGGERED','NO_TRADE'])
def test_current_status_is_preserved_without_new_scoring_gates(state):
    _,_,_,item,row,kwargs = context()
    row['status'] = row['analysis']['status'] = state
    assert service.reading(item,row,**kwargs)['status'] == state


@pytest.mark.parametrize('change,reason', [
    ('missing','EVALUATION_UNAVAILABLE'), ('stale','EVALUATION_STALE'),
    ('reference','REFERENCE_PROFILE_CHANGED'), ('profile','PROFILE_CHANGED'),
    ('config','CONFIG_CHANGED'), ('indicator','INDICATOR_CONFIG_CHANGED'),
    ('engine','ENGINE_CHANGED'), ('quality','EVALUATION_DATA_PENDING'),
    ('future','INVALID_SOURCE_TIME'), ('time','MISSING_SOURCE_TIME'),
])
def test_invalid_armeds_remain_watchlist_only(change,reason):
    _,_,_,item,row,kwargs = context()
    if change=='missing': row=None
    elif change=='stale': kwargs['now'] += timedelta(seconds=kwargs['cfg'].stale_tf_multiplier*300)
    elif change=='reference': item['profile_id']=str(uuid4())
    elif change=='profile': row['analysis']['profile_hash']='old'
    elif change=='config': row['analysis']['config_hash']='old'
    elif change=='indicator': row['analysis']['indicator_identity']={}
    elif change=='engine': row['analysis']['engine_version']='old'
    elif change=='quality': row['quality']='pending'
    elif change=='future': row['analysis']['candle_close_at_utc']=(NOW+timedelta(seconds=1)).isoformat()
    elif change=='time': row['analysis']['candle_close_at_utc']=None
    result=service.reading(item,row,**kwargs)
    assert result['status'] is None and reason in result['reason_codes']
    assert item['status']=='approved'


def test_real_database_latest_reference_and_full_universe(database, monkeypatch):
    db,uid=database; migrate(db)
    _,pid,cid,item,row,kwargs=context()
    kwargs['indicator_identity']['config_user_id']=str(uid)
    row['analysis']['indicator_identity']=kwargs['indicator_identity']
    db.execute(sa.text('CREATE TABLE profiles(id uuid,user_id uuid,config jsonb,is_active boolean)'))
    db.execute(sa.text("INSERT INTO profiles VALUES(:p,:u,'{}',true)"),{'p':pid,'u':uid})
    db.execute(sa.text("INSERT INTO config_profiles(id,user_id,config_type,config_json,is_active) VALUES(:c,:u,'indicators',:j,true)"),
               {'c':cid,'u':uid,'j':json.dumps({'rsi':{}})})
    items=[dict(item,symbol=f'ASSET_{n}') for n in range(125)]
    pending=[dict(item,symbol='PENDING',status='awaiting_data',data_reasons=['FEATURE_TTL_EXPIRED'])]
    base=dict(id='l3-consolidated',items=items,awaiting_data_items=pending,total=len(items),awaiting_data_count=1,as_of=NOW.isoformat())
    monkeypatch.setattr(service,'load_l3_profile_consolidation',AsyncMock(return_value=base))
    monkeypatch.setattr(service,'load_config',AsyncMock(return_value=kwargs['cfg']))
    def insert(symbol,*,at=NOW,state='ARMED',owner=uid,profile=pid,wid='l3-consolidated'):
        a={**row['analysis'],'status':state,'profile_id':str(profile)}
        db.execute(sa.text('''INSERT INTO mtf_history_observations
            (id,user_id,watchlist_id,symbol,profile_id,identity,observed_at,status,quality,payload)
            VALUES(:id,:uid,:wid,:s,:p,:key,:at,:state,'current',CAST(:j AS jsonb))'''),
            dict(id=uuid4(),uid=owner,wid=wid,s=symbol,p=str(profile),key=str(uuid4()),at=at,state=state,
                 j=json.dumps({'operational_effect':False,'analysis':a})))
    for item in items: insert(item['symbol'])
    # An older ARMED must not mask a later WAIT, even when future labels exist.
    insert('ASSET_0',at=NOW-timedelta(seconds=1))
    insert('ASSET_1',at=NOW-timedelta(seconds=1),state='WAIT')
    # Isolate a newer foreign user/watchlist; neither may replace our current row.
    other=uuid4();db.execute(sa.text('INSERT INTO users VALUES(:u)'),{'u':other})
    insert('ASSET_0',owner=other,state='NO_TRADE')
    insert('ASSET_0',wid='different',state='NO_TRADE')
    async def run():
        adapter=Adapter(db)
        first=await service.load_l3_armed_display(adapter,user_id=uid)
        assert first['armed_count']==125 and first['total']==125
        assert first['awaiting_data_items']==pending and all('mtf' not in i for i in pending)
        # Advance the read clock and publish a later WAIT for the reference profile.
        base['as_of']=(NOW+timedelta(seconds=1)).isoformat()
        insert('ASSET_0',at=NOW+timedelta(seconds=1),state='WAIT')
        # Latest contribution from a different profile does not arm the reference.
        insert('ASSET_1',at=NOW+timedelta(seconds=1),profile=uuid4())
        display=await service.load_l3_armed_display(adapter,user_id=uid)
        external=await service.load_l3_armed_assets(adapter,user_id=uid)
        assert display['total']==125 and display['armed_count']==123
        assert {i['symbol'] for i in external['items']}=={i['symbol'] for i in display['armed_items']}
        assert external['observation_contract']=='L3_ARMED_OBSERVATION_V1'
        assert external['executable'] is external['execution_authority'] is False
        assert all(i['status']=='ARMED' and i['profile_status']=='approved' and not i['executable'] for i in external['items'])
        assert all('authorization_id' not in i and 'shadow_id' not in i for i in external['items'])
        assert base['items']==items and all('mtf' not in i for i in items)
    asyncio.run(run())


@pytest.mark.asyncio
async def test_internal_observer_never_calls_filtered_external_feed(monkeypatch):
    from app.api import watchlists
    from app.services import mtf_chart_service
    legacy=AsyncMock(return_value={'items':[{'symbol':'WAIT_USDT'}]})
    external=AsyncMock(side_effect=AssertionError('would restrict observation to ARMED'))
    monkeypatch.setattr(watchlists,'load_l3_internal_candidates',legacy)
    monkeypatch.setattr(watchlists,'list_l3_consolidated_assets',external)
    assert await mtf_chart_service.members(object(),uuid4(),'l3-consolidated') == [{'symbol':'WAIT_USDT'}]
    external.assert_not_awaited()


def test_existing_external_url_is_authenticated_read_only(monkeypatch):
    from app.api import watchlists
    app=FastAPI();app.include_router(watchlists.router)
    app.dependency_overrides[watchlists.get_db]=lambda:object()
    http=TestClient(app)
    url='/api/watchlists/l3-consolidated/assets'
    assert http.get(url).status_code in (401,403)
    uid=uuid4();app.dependency_overrides[watchlists.get_current_user_id]=lambda:uid
    loader=AsyncMock(return_value={'items':[],'total':0,'executable':False,'execution_authority':False})
    monkeypatch.setattr(service,'load_l3_armed_assets',loader)
    response=http.get(url)
    assert response.status_code==200 and response.json()['items']==[]
    assert response.headers['cache-control']=='private, no-store'
    assert loader.await_args.kwargs['user_id']==uid
