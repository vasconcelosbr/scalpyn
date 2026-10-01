"""Capture atomic commit/ack loss and owner lock recovery; TEMP tables only."""
import asyncio,json,os,sys,argparse
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--temp-only',action='store_true',required=True)
parser.parse_args()
url=os.environ.get('DATABASE_URL')
if not url:raise SystemExit('Use an existing authorized DATABASE_URL; no credentials are created by this script')
os.environ['DATABASE_URL']=url.replace('postgresql://','postgresql+asyncpg://')
os.environ['REDIS_URL']=''
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine,AsyncSession
from app import database
from app.services import pump_opportunity_service as svc,pump_opportunity_engine as eng
import importlib.util

async def main():
    engine=create_async_engine(os.environ['DATABASE_URL'],connect_args={'timeout':10},hide_parameters=True)
    owner=uuid4();other=uuid4();mode='normal';ddl=[]
    for name in ('231_pump_opportunity_v2.py','232_pump_exact_price_paths.py','233_pump_label_queue.py','234_pump_ml_job_ledger.py','235_pump_label_resource_block.py'):
        spec=importlib.util.spec_from_file_location('fixture',root/'alembic/versions'/name)
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        migration.op.execute=lambda sql:ddl.append(str(sql));migration.upgrade()
    async with engine.connect() as conn,engine.connect() as peer:
        async with conn.begin():
            for sql in ddl:
                sql=sql.replace('CREATE TABLE','CREATE TEMP TABLE')
                sql=sql.replace('ALTER TABLE pump_opportunity_label_queue ','ALTER TABLE pg_temp.pump_opportunity_label_queue ')
                await conn.execute(text(sql))
            await conn.execute(text('CREATE TEMP TABLE config_profiles(user_id uuid,config_type text,pool_id uuid,is_active boolean,updated_at timestamptz,config_json jsonb)'))
            await conn.execute(text("INSERT INTO pg_temp.config_profiles VALUES(:u,'pump_opportunity',NULL,true,now(),CAST(:p AS jsonb))"),{'u':owner,'p':json.dumps(eng.config({'enabled':True,'price_paths_enabled':True}))})
        async with conn.begin():
            for name in ('config_profiles','pump_opportunity_observations','pump_opportunity_price_paths','pump_opportunity_label_queue'):
                assert (await conn.execute(text("SELECT c.relnamespace=pg_my_temp_schema() FROM pg_class c WHERE c.oid=to_regclass(:name)"),{'name':name})).scalar()
        async def run(fn,**kwargs):
            async with conn.begin():
                async with AsyncSession(bind=conn,expire_on_commit=False) as db:
                    result=await fn(db)
                    if mode=='before_commit' and isinstance(result,dict) and result.get('written')==100:raise TimeoutError('before_commit')
            if mode=='after_commit' and isinstance(result,dict) and result.get('written')==100:raise TimeoutError('ack_loss')
            return result
        database.run_db_task=run
        async def counts():
            async with conn.begin():
                return tuple((await conn.execute(text('SELECT (SELECT count(*) FROM pg_temp.pump_opportunity_observations),(SELECT count(*) FROM pg_temp.pump_opportunity_price_paths),(SELECT count(*) FROM pg_temp.pump_opportunity_label_queue)'))).one())
        now=datetime.now(timezone.utc);ms=int(now.timestamp()//60-1)*60000
        row={'symbol':'ATOMIC_FIXTURE_USDT','indicators':{}}
        collected={row['symbol']:{'book':{'asks':[[100,100]],'bids':[[99.99,100]],'observed_at':now.isoformat()},'raw_price_input':{'source':'rest_fallback','trades':[{'trade_id':'fixture','ts_ms':ms+1000,'price':100}],'covered_from_ms':ms-60000},'buckets':[{'bucket_start_ms':ms,'partial':False}]}}
        rows=[{**row,'symbol':f'ATOMIC{i}_USDT'} for i in range(100)]
        collected={r['symbol']:collected[row['symbol']] for r in rows}
        args=(owner,rows,collected,{}, {'_meta':{'config_hash':'fixture'},'universe_filter':{'min_market_cap_usd':1e9}})
        mode='before_commit'
        try:await svc.ingest(*args);raise AssertionError('injection_failed')
        except TimeoutError as exc:
            if str(exc)!='before_commit':raise
        assert await counts()==(0,0,0)
        mode='after_commit'
        try:await svc.ingest(*args);raise AssertionError('injection_failed')
        except TimeoutError as exc:
            if str(exc)!='ack_loss':raise
        assert await counts()==(100,100,600)
        async with conn.begin():
            stored=(await conn.execute(text('SELECT observation_id,payload FROM pg_temp.pump_opportunity_observations ORDER BY observation_id'))).all()
        mode='normal';retry=await svc.ingest(*args);assert await counts()==(100,100,600)
        assert retry['written']==retry['price_paths_written']==0 and retry['duplicates']==100
        async with conn.begin():
            assert (await conn.execute(text('SELECT observation_id,payload FROM pg_temp.pump_opportunity_observations ORDER BY observation_id'))).all()==stored
        lock=text("SELECT pg_try_advisory_xact_lock(hashtextextended('pump_capture:'||CAST(:u AS text),0))")
        tx=await conn.begin();peer_tx=await peer.begin()
        assert (await conn.execute(lock,{'u':str(owner)})).scalar()
        assert not (await peer.execute(lock,{'u':str(owner)})).scalar()
        assert (await peer.execute(lock,{'u':str(other)})).scalar()
        await tx.rollback();await peer_tx.rollback()
        async with peer.begin():assert (await peer.execute(lock,{'u':str(owner)})).scalar()
        print(json.dumps({'before_commit_counts':[0,0,0],'after_ack_loss_counts':[100,100,600],'retry_counts':[100,100,600],'owner_lock_exclusive':True,'other_owner_independent':True,'lock_recovered_after_rollback':True,'production_writes':0}))
    await engine.dispose()
asyncio.run(main())
