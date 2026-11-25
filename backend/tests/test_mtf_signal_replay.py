import asyncio
import json
import importlib.util
from pathlib import Path
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import Response, HTTPException
from app.schemas.mtf_history import HistoryConfig
from app.services.mtf_signal_replay import activation, barrier, record, advance_one
from test_mtf_chart_postgres import database
from test_mtf_history import migrate, Adapter


def test_activation_latch_reactivation_and_out_of_order():
    at=datetime(2026,1,1,tzinfo=timezone.utc)
    assert activation(None,'TRIGGERED',at)==(True,True)
    prev={'candle_at':at,'status':'TRIGGERED'}
    assert activation(prev,'ARMED',at)==(False,False)
    assert activation(prev,'TRIGGERED',at+timedelta(minutes=5))==(True,False)
    assert activation(dict(prev,status='ARMED'),'TRIGGERED',at+timedelta(minutes=5))==(True,True)


@pytest.mark.parametrize('o,h,l,status,price',[(100,102,99,'TARGET',101),(100,100.5,97,'STOP',98),
    (100,102,97,'INDETERMINATE',None),(102,103,97,'TARGET',102),(97,102,96,'STOP',97),
    (100,100.5,99,None,None)])
def test_barrier_chronology(o,h,l,status,price):
    result=barrier(dict(open=o,high=h,low=l),100,101,98)
    if status is None: assert result is None
    else:
        assert result['status']==status
        assert result.get('price')==price
        assert result['return_pct']==(None if price is None else pytest.approx(price-100))


def test_missing_stop_never_invents_loss():
    assert barrier(dict(open=100,high=100,low=1),100,101,None) is None


def test_export_has_all_candles_and_raw_snapshots_with_owner_isolation(replay_db):
    from app.api.mtf_signal_replay import export_json
    db,uid=replay_db;adapter=Adapter(db)
    at=datetime(2026,1,1,tzinfo=timezone.utc)
    async def check():
        original=payload(at)
        original['indicators']=[{'name':'rsi','value':None,'quality':'missing'}]
        await record(adapter,uid,'H4',original,HistoryConfig())
        rid=db.scalar(sa.text('SELECT id FROM mtf_signal_replays'))
        db.execute(sa.text('''INSERT INTO mtf_replay_candles(replay_id,time,payload)
            SELECT :rid, CAST(:at AS timestamptz)+i*interval '1 minute',
                jsonb_build_object('time',CAST(:at AS timestamptz)+i*interval '1 minute','close',100)
            FROM generate_series(0,1001) i'''),dict(rid=rid,at=at))
        db.execute(sa.text('''INSERT INTO mtf_replay_snapshots(replay_id,identity,captured_at,payload)
            VALUES(:rid,'a',:at,CAST(:a AS jsonb)),
                  (:rid,'b',:at,CAST(:b AS jsonb))'''),dict(rid=rid,at=at,
                      a=json.dumps({'indicators':[{'name':'rsi','value':42}]}),
                      b=json.dumps({'indicators':[{'name':'adx','value':25}]})))
        response=await export_json(rid,db=adapter,uid=uid)
        exported=json.loads(response.body)
        assert exported['schema_version']=='mtf_signal_replay_export_v1'
        assert len(exported['candles'])==1002  # Not limited by trajectory pagination.
        assert len(exported['snapshots'])==2  # Same timestamp must not discard a capture.
        assert exported['replay']['snapshot']['indicators']==original['indicators']
        assert exported['replay']['snapshot']['replay_inputs']=={'frozen':True}
        assert exported['progress']['status']=='OPEN' and exported['metadata']['ongoing']
        assert exported['historical_observations']==[]
        assert response.headers['cache-control']=='private, no-store'
        assert response.headers['content-disposition'].endswith('.json"')
        with pytest.raises(HTTPException) as exc:
            await export_json(rid,db=adapter,uid=uuid4())
        assert exc.value.status_code==404
    asyncio.run(check())


@pytest.fixture
def replay_db(database):
    db,uid=database
    migrate(db)
    path=Path(__file__).parents[1]/'alembic/versions/252_mtf_signal_replay.py'
    spec=importlib.util.spec_from_file_location('replay_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db)):module.upgrade()
    return db,uid


def payload(at,status='TRIGGERED'):
    return dict(analysis=dict(symbol='BTC_USDT',profile_id=None,status=status,
        candle_close_at_utc=at.isoformat(),candle_closed_5m=(at-timedelta(minutes=5)).isoformat(),plan={'stop':98}),
        candle_close_price=100,observed_at=(at+timedelta(seconds=8)).isoformat(),
        frames={'5m':{'last_closed_candle':{'time':at.timestamp()-300,'close':100}}},indicators=[],
        replay_inputs={'frozen':True},history_config={},engine_config={})


def test_independent_signals_idempotence_and_owner_scoping(replay_db):
    from app.api.mtf_signal_replay import listing,detail,trajectory
    db,uid=replay_db;adapter=Adapter(db)
    at=datetime(2026,1,1,tzinfo=timezone.utc)
    async def check():
        await record(adapter,uid,'H4',payload(at,'ARMED'),HistoryConfig())
        p=payload(at+timedelta(minutes=5))
        await record(adapter,uid,'H4',p,HistoryConfig())
        await record(adapter,uid,'H4',p,HistoryConfig())
        await record(adapter,uid,'H4',payload(at+timedelta(minutes=10)),HistoryConfig())
        assert db.scalar(sa.text('SELECT count(*) FROM mtf_signal_replays'))==1
        await record(adapter,uid,'H4',payload(at+timedelta(minutes=15),'ARMED'),HistoryConfig())
        await record(adapter,uid,'H4',payload(at+timedelta(minutes=20)),HistoryConfig(replay_target_pct=2))
        await record(adapter,uid,'H1',p,HistoryConfig())
        assert db.scalar(sa.text("SELECT count(*) FROM mtf_replay_progress WHERE status='OPEN'"))==3
        assert db.scalar(sa.text('SELECT count(*) FROM mtf_chart_shadow_events'))==0
        assert db.scalar(sa.text('SELECT max(target) FROM mtf_signal_replays'))==102
        r=await listing(Response(),offset=0,limit=50,db=adapter,uid=uid)
        assert len(r['items'])==3
        assert (await listing(Response(),offset=0,limit=50,db=adapter,uid=uuid4()))['items']==[]
        rid=r['items'][0]['id']
        assert (await detail(rid,Response(),db=adapter,uid=uid))['armed'][0]['time']==(at+timedelta(minutes=15)).isoformat()
        assert (await trajectory(rid,Response(),limit=100,db=adapter,uid=uid))['candles']==[]
        for endpoint in (detail,trajectory):
            with pytest.raises(HTTPException) as exc: await endpoint(rid,Response(),db=adapter,uid=uuid4())
            assert exc.value.status_code==404
        with pytest.raises(sa.exc.DBAPIError,match='MTF_HISTORY_IMMUTABLE'):
            with db.begin_nested():
                db.execute(sa.text("UPDATE mtf_signal_replays SET baseline=99 WHERE id=:id"),{'id':rid})
    asyncio.run(check())


def test_restart_gap_then_target_and_immutable_candles(replay_db):
    from app.services.research_ohlcv_service import GateClosedCandleBatch
    db,uid=replay_db;adapter=Adapter(db)
    at=datetime(2026,1,1,tzinfo=timezone.utc)
    async def check():
        await record(adapter,uid,'H4',payload(at),HistoryConfig())
        row=dict(db.execute(sa.text('SELECT r.*,p.next_at FROM mtf_signal_replays r JOIN mtf_replay_progress p ON p.replay_id=r.id')).mappings().one())
        bars=[dict(time=at+timedelta(minutes=i),open=100,high=100.5 if i==0 else 102,low=99,close=100,exchange='gate.io') for i in range(2)]
        batch=GateClosedCandleBatch('BTC_USDT','1m',at+timedelta(minutes=3),tuple(bars[1:]),0,None,None)
        with patch('app.services.research_ohlcv_service.fetch_gate_closed_candles',AsyncMock(return_value=batch)):
            await advance_one(adapter,row)
        progress=db.execute(sa.text('SELECT * FROM mtf_replay_progress')).mappings().one()
        assert progress['issue']=='CANDLE_GAP' and progress['next_at']==at and progress['status']=='OPEN'
        batch=GateClosedCandleBatch('BTC_USDT','1m',at+timedelta(minutes=3),tuple(bars),0,None,None)
        with patch('app.services.research_ohlcv_service.fetch_gate_closed_candles',AsyncMock(return_value=batch)):
            await advance_one(adapter,row)
        progress=db.execute(sa.text('SELECT * FROM mtf_replay_progress')).mappings().one()
        assert progress['status']=='TARGET' and progress['result']['return_pct']==pytest.approx(1)
        assert db.scalar(sa.text('SELECT count(*) FROM mtf_replay_candles'))==2
    asyncio.run(check())


def test_bootstrap_uses_frozen_history_not_current_prices(replay_db):
    from app.services.mtf_history import serial
    db,uid=replay_db;adapter=Adapter(db)
    at=datetime(2026,1,1,tzinfo=timezone.utc)
    for i,status in enumerate(['ARMED','TRIGGERED','TRIGGERED','NO_TRADE','ARMED','TRIGGERED']):
        p=payload(at+timedelta(minutes=5*i),status)
        p['operational_effect']=False
        db.execute(sa.text('''INSERT INTO mtf_history_observations
            (id,user_id,watchlist_id,symbol,identity,observed_at,status,quality,payload)
            VALUES(:id,:uid,'H4','BTC_USDT',:key,:at,:status,'current',CAST(:p AS jsonb))'''),
            dict(id=uuid4(),uid=uid,key=str(i),at=at+timedelta(minutes=5*i,seconds=8),status=status,p=serial(p)))
    async def check():
        current=payload(at+timedelta(minutes=30))
        current['candle_close_price']=150
        await record(adapter,uid,'H4',current,HistoryConfig())
        rows=db.execute(sa.text('SELECT * FROM mtf_signal_replays ORDER BY triggered_at')).mappings().all()
        assert len(rows)==2 and all(r['baseline']==100 for r in rows)
        assert all(r['snapshot']['capture_kind']=='reconstructed_from_frozen_observations' for r in rows)
        assert len(rows[-1]['armed'])==1
        from app.api.mtf_signal_replay import trajectory
        result=await trajectory(rows[0]['id'],Response(),limit=100,db=adapter,uid=uid)
        assert result['next_cursor'] is None
        from app.api.mtf_signal_replay import export_json
        db.execute(sa.text('UPDATE mtf_replay_progress SET next_at=:until WHERE replay_id=:id'),
                   {'id':rows[0]['id'],'until':at+timedelta(minutes=11)})
        exported=json.loads((await export_json(rows[0]['id'],db=adapter,uid=uid)).body)
        assert len(exported['historical_observations'])==2
        assert all(h['payload']['replay_inputs']=={'frozen':True}
                   for h in exported['historical_observations'])
        stale=payload(at+timedelta(minutes=35),'NO_TRADE')
        stale['analysis']['vetoes']=['DATA_STALE:5m']
        await record(adapter,uid,'H4',stale,HistoryConfig())
        await record(adapter,uid,'H4',payload(at+timedelta(minutes=40)),HistoryConfig())
        assert db.scalar(sa.text('SELECT count(*) FROM mtf_signal_replays'))==2
    asyncio.run(check())
