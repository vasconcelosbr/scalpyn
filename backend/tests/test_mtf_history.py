"""History is immutable observational evidence, including non-trading states."""
import asyncio
import importlib.util
import io
import json
import zipfile
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import AsyncMock,patch
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.schemas.mtf_chart import MTFChartConfig,experimental_preset
from app.schemas.mtf_history import HistoryConfig,HistoryFilter
from app.services.mtf_history import snapshot,outcome,utc
from test_mtf_chart_postgres import database


def sample():
    at=datetime(2026,1,1,12,0,tzinfo=timezone.utc)
    return {'analysis':{'symbol':'ETH_USDT','observed_at_utc':at.isoformat(),'status':'WAIT',
        'context_4h':{'trend':'UP'},'score':{'total':3},'candle_close_at_utc':at.isoformat(),
        'config_hash':'engine','profile_hash':'profile','engine_version':'mtf_chart_v1'},
        'quote':{'price':100,'received_at':at.isoformat()},
        'frames':{'5m':{'candles':[{'time':at.timestamp()-300,'close':99}],
            'indicators':{'rsi':[{'time':at.timestamp()-300,'value':45}]},'pivots':[]}},
        'indicator_config':{'rsi':{'period':14}}}


def test_snapshot_times_and_missing_values_are_not_fabricated():
    cfg=MTFChartConfig.model_validate(experimental_preset())
    data=sample()
    p=snapshot(data,{},cfg,HistoryConfig(),data['analysis']['observed_at_utc'])
    assert p['observed_price']==100 and p['candle_close_price']==99
    assert p['price_source_at'] is None and p['price_received_at'] is not None
    assert p['indicators'][0]['value']==45 and p['indicators'][0]['source_at']==p['observed_at']
    data['quote']=None
    p=snapshot(data,{'current_price':123,'evaluated_at':p['observed_at']},cfg,HistoryConfig(),p['observed_at'])
    assert p['observed_price'] is None and p['price_source_at'] is None
    assert p['quality']=='pending' and 'QUOTE_UNAVAILABLE' in p['data_reasons']


def test_future_labels_exclude_pre_observation_and_detect_gaps():
    at=utc('2026-01-01T12:00:10+00:00')
    p={'observed_at':at.isoformat(),'observed_price':100,'price_quality':'current','price_received_at':at.isoformat(),
       'candle_close_price':99,'candle_close_at':at.replace(second=0).isoformat(),'engine_config':{'stale_tf_multiplier':2}}
    candles=[{'time':at.replace(second=0)+timedelta(minutes=m),'close':110,'high':120 if m else 999,
        'low':90,'is_closed':True,'exchange':'gate.io','ingested_at':at} for m in (0,5,10,15)]
    assert outcome(p,15,candles,at+timedelta(minutes=14))=={'status':'PENDING'}
    result=outcome(p,15,candles,at+timedelta(minutes=25))
    assert result['status']=='COMPLETE' and result['high']==120
    assert result['return_pct']==pytest.approx(10) and result['available_candles']==3
    assert outcome(p,15,candles[:-1],at+timedelta(minutes=25))['status']=='INCOMPLETE'
    assert outcome(p,15,[dict(c,is_closed=False) for c in candles],at+timedelta(minutes=25))['return_pct'] is None


def migrate(db):
    path=Path(__file__).parents[1]/'alembic/versions/248_mtf_history.py'
    spec=importlib.util.spec_from_file_location('history_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db)):module.upgrade()
    db.execute(sa.text('''CREATE TABLE indicators(time timestamptz,symbol text,timeframe text,
        market_type text,scheduler_group text,indicators_json jsonb)'''))


class Stream:
    def __init__(self,rows):self.rows=iter(rows)
    def __aiter__(self):return self
    async def __anext__(self):
        try:return next(self.rows)
        except StopIteration:raise StopAsyncIteration
    async def close(self):pass


class Adapter:
    def __init__(self,db):self.db=db
    async def scalar(self,statement,params=None):return self.db.scalar(statement,params)
    async def execute(self,statement,params=None):return self.db.execute(statement,params)
    async def stream(self,statement,params=None):return Stream(self.db.execute(statement,params).all())
    async def commit(self):pass


def test_capture_idempotence_history_removal_isolation_and_exports(database):
    from app.services import mtf_history as h
    from app.services.mtf_history_export import build
    from app.api.mtf_history import observation,download,export_status
    from fastapi import HTTPException,Response
    db,uid=database;migrate(db)
    adapter=Adapter(db)
    cfg=MTFChartConfig.model_validate(experimental_preset())
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,*args,**kwargs):
            class Reply:
                def raise_for_status(self):pass
                def json(self):return [{'last':'100'}]
            return Reply()
    async def run():
        with patch.object(h.chart,'chart_data',AsyncMock(return_value=sample())),patch.object(h.chart,'load_config',AsyncMock(return_value=cfg)),\
             patch.object(h.config_service,'get_config',AsyncMock(return_value=HistoryConfig().model_dump())),patch('httpx.AsyncClient',return_value=Client()):
            assert await h.capture(adapter,uid,'l3-consolidated',{'symbol':'ETH_USDT'},'2026-01-01T12:00:00+00:00')
            assert not await h.capture(adapter,uid,'l3-consolidated',{'symbol':'ETH_USDT'},'2026-01-01T12:00:00+00:00')
        assert db.scalar(sa.text('SELECT count(*) FROM mtf_chart_shadow_events'))==0
        listing=await h.list_observations(adapter,uid,HistoryFilter())
        assert len(listing['items'])==1
        oid=listing['items'][0]['id']
        assert len((await h.detail(adapter,uid,oid))['outcomes'])==4
        assert await h.detail(adapter,uuid4(),oid) is None
        assert not (await h.list_observations(adapter,uuid4(),HistoryFilter()))['items']
        with pytest.raises(HTTPException) as error:await observation(oid,Response(),db=adapter,uid=uuid4())
        assert error.value.status_code==404
        # Membership does not participate in historical ownership checks.
        with patch.object(h,'history_members',side_effect=AssertionError('must not consult live membership')):
            assert await observation(oid,Response(),db=adapter,uid=uid)
        raw,n=await build(adapter,uid,{'format':'csv'},datetime.now(timezone.utc)+timedelta(seconds=1))
        assert n==1
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            assert {'observations.csv','indicators.csv','outcomes.csv'} <= set(z.namelist())
            assert str(oid) in z.read('observations.csv').decode('utf-8-sig')
        raw,n=await build(adapter,uid,{'format':'xlsx'},datetime.now(timezone.utc)+timedelta(seconds=1))
        from openpyxl import load_workbook
        book=load_workbook(io.BytesIO(raw),read_only=True)
        assert book['observations']['A2'].value==str(oid)
        assert book['indicators']['B2'].value=='rsi'
        assert len(list(book['outcomes'].rows))==5
        book.close()
        eid=uuid4()
        db.execute(sa.text("INSERT INTO mtf_history_exports(id,user_id,request,status,content) VALUES(:id,:uid,'{\"format\":\"xlsx\"}','READY',:content)"),{'id':eid,'uid':uid,'content':raw})
        for endpoint in (download,export_status):
            with pytest.raises(HTTPException) as error:await endpoint(eid,db=adapter,uid=uuid4())
            assert error.value.status_code==404
        response=await download(eid,db=adapter,uid=uid)
        assert response.body==raw
    asyncio.run(run())
    with db.begin_nested():
        with pytest.raises(sa.exc.DBAPIError,match='MTF_HISTORY_IMMUTABLE'):
            with db.begin_nested():db.execute(sa.text("UPDATE mtf_history_observations SET status='TRIGGERED'"))


def test_history_queue_cannot_share_indicator_worker(monkeypatch):
    from app.tasks.celery_app import _configured_task_modules,TASK_ROUTES
    monkeypatch.setenv('WORKER_QUEUES','mtf_history')
    assert _configured_task_modules()==('app.tasks.mtf_history',)
    monkeypatch.setenv('WORKER_QUEUES','mtf_history,indicator_5m')
    with pytest.raises(ValueError):_configured_task_modules()
    assert all(TASK_ROUTES['app.tasks.mtf_history.'+name]['queue']=='mtf_history' for name in ('dispatch','capture','maintenance'))


def test_history_settings_do_not_change_strategy_hash():
    cfg=MTFChartConfig.model_validate(experimental_preset())
    before=cfg.engine_dump()
    HistoryConfig(horizons_minutes=[60,15,1440,240,15])
    assert cfg.engine_dump()==before
    with pytest.raises(ValueError):HistoryConfig(horizons_minutes=[-1])
    with pytest.raises(ValueError):HistoryFilter(start=datetime(2026,1,1))


def test_full_consolidated_universe_does_not_consult_shadow_limit():
    from app.services.mtf_history import history_members
    items=[{'symbol':f'COIN_{n}'} for n in range(125)]
    pending=[{'symbol':'PENDING_USDT'}]
    async def run():
        with patch('app.services.l3_profile_consolidation.load_l3_profile_consolidation',AsyncMock(return_value={'items':items,'awaiting_data_items':pending})),\
             patch('app.services.mtf_chart_service.members',side_effect=AssertionError('execution feed must not be read')):
            assert await history_members(None,uuid4(),'l3-consolidated')==items+pending
    asyncio.run(run())


def test_capture_outbox_recovers_unacknowledged_jobs(database):
    from app.tasks.mtf_history import dispatch_pending
    db,uid=database;migrate(db)
    db.execute(sa.text("INSERT INTO mtf_history_capture_jobs(identity,user_id,watchlist_id,member,scheduled_at) VALUES('recover',:uid,'removed','{}',now())"),{'uid':uid})
    async def run():
        with patch('app.tasks.task_dispatch.enqueue',return_value='delivery') as enqueue:
            await dispatch_pending(Adapter(db));assert enqueue.call_count==1
            await dispatch_pending(Adapter(db));assert enqueue.call_count==1
            db.execute(sa.text("UPDATE mtf_history_capture_jobs SET dispatched_at=now()-interval '11 minutes'"))
            await dispatch_pending(Adapter(db));assert enqueue.call_count==2
            assert enqueue.call_args.kwargs['args']==('recover',)
    asyncio.run(run())


def test_bad_filters_return_validation_error():
    from app.api.mtf_history import history_filters
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:history_filters(start=datetime(2026,1,1))
    assert e.value.status_code==422


def test_mature_outcome_follows_removed_asset_without_current_membership(database):
    from app.services import mtf_history as h
    db,uid=database;migrate(db)
    db.execute(sa.text('''CREATE TABLE ohlcv(time timestamptz,symbol text,market_type text,timeframe text,
        exchange text,is_closed boolean,ingested_at timestamptz,close numeric,high numeric,low numeric)'''))
    at=utc('2026-01-01T12:00:00+00:00');oid=uuid4()
    payload={'operational_effect':False,'observed_at':at.isoformat(),'observed_price':100,'price_quality':'current','price_received_at':at.isoformat()}
    db.execute(sa.text('''INSERT INTO mtf_history_observations(id,user_id,watchlist_id,symbol,identity,observed_at,status,quality,payload)
        VALUES(:id,:uid,'removed','REMOVED_USDT','removed-key',:at,'WAIT','current',CAST(:p AS jsonb))'''),{'id':oid,'uid':uid,'at':at,'p':json.dumps(payload)})
    db.execute(sa.text('INSERT INTO mtf_history_outcomes(observation_id,horizon_minutes,due_at) VALUES(:id,15,:due)'),{'id':oid,'due':at+timedelta(minutes=15)})
    candles=[dict(time=at+timedelta(minutes=m),close=110,high=120,low=90,is_closed=True,exchange='gate.io',ingested_at=at+timedelta(minutes=20)) for m in (0,5,10)]
    async def run():
        with patch.object(h,'future_candles',AsyncMock(return_value=candles)) as read,patch.object(h,'history_members',side_effect=AssertionError('membership must not block labels')):
            assert await h.advance_outcomes(Adapter(db))==1
            read.assert_awaited_once()
            assert await h.advance_outcomes(Adapter(db))==0
        row=db.execute(sa.text('SELECT status,payload FROM mtf_history_outcomes')).mappings().one()
        assert row['status']=='COMPLETE' and row['payload']['return_pct']==pytest.approx(10)
    asyncio.run(run())
