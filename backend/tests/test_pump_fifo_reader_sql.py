"""Optional PostgreSQL equivalence proof using SELECT-only in-memory fixtures.

PUMP_READER_TEST_DATABASE_URL enables this suite. It creates no database objects,
reads no production table and sets the entire transaction READ ONLY.
"""
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime,timedelta,timezone
from uuid import UUID
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.services.pump_label_reader import FIFO_PREFIX_SQL

URL=os.environ.get('PUMP_READER_TEST_DATABASE_URL')
pytestmark=pytest.mark.skipif(not URL,reason='SELECT-only PostgreSQL fixture URL not configured')
OLD_SQL=(Path(__file__).parent/'fixtures'/'pump_fifo_reader_before.sql').read_text()
OWNER=str(UUID(int=1));INSTRUMENT=str(UUID(int=2));OTHER=str(UUID(int=3))
NOW=datetime(2026,1,1,tzinfo=timezone.utc)

def fixture_sql(sql):
    source="""pump_opportunity_price_paths AS MATERIALIZED (
        SELECT * FROM jsonb_to_recordset(CAST(:fixture_paths AS jsonb)) AS p(
            user_id uuid,instrument_id uuid,bucket_start timestamptz,
            complete boolean,captured_at timestamptz,payload jsonb)), """
    return sql.replace('WITH RECURSIVE ', 'WITH RECURSIVE '+source,1) if sql.startswith('WITH RECURSIVE') else sql.replace('WITH ', 'WITH '+source,1)

def data():
    paths=[]
    for instrument in (INSTRUMENT,OTHER):
        for minute in range(8):
            at=NOW+timedelta(minutes=minute)
            for revision in (0,1):
                paths.append({'user_id':OWNER,'instrument_id':instrument,'bucket_start':at.isoformat(),
                    'complete':revision==1,'captured_at':(at+timedelta(seconds=revision)).isoformat(),
                    'payload':{'bucket_start_ms':int(at.timestamp()*1000),'complete':revision==1,
                        'points':[[at.timestamp()*1000+1000,100+minute/10,f'{revision}:{minute}']]*(minute+1)}})
    windows=[(INSTRUMENT,0,2,True),(INSTRUMENT,1,3,True),(OTHER,0,5,True),
        (INSTRUMENT,0,7,False),(INSTRUMENT,6,7,True),(str(UUID(int=4)),0,3,True)]
    requests=[{'n':n,'i':i,'s':'FIXTURE','a':(NOW+timedelta(minutes=a)).isoformat(),
        'b':(NOW+timedelta(minutes=b,seconds=3)).isoformat(),'exact':exact}
        for n,(i,a,b,exact) in enumerate(windows,1)]
    return paths,requests

@pytest.mark.parametrize('points',[0,1,6,15,1000])
@pytest.mark.parametrize('byte_limit',[0,128,2048,100000])
def test_sql_fifo_prefix_matches_previous_reader(points,byte_limit):
    async def run():
        engine=create_async_engine(URL,connect_args={'timeout':8,'command_timeout':4})
        try:
            async with engine.connect() as db:
                await db.execute(text('SET TRANSACTION READ ONLY'))
                await db.execute(text("SET LOCAL statement_timeout='3000ms'"))
                paths,requests=data()
                params={'u':UUID(OWNER),'requests':json.dumps(requests),'count':len(requests),
                    'points':points,'bytes':byte_limit,'fixture_paths':json.dumps(paths)}
                old=list((await db.execute(text(fixture_sql(OLD_SQL)),params)).mappings())
                new=list((await db.execute(text(fixture_sql(FIFO_PREFIX_SQL)),params)).mappings())
                for key in ('points','bytes','accepted_count','exhausted','head_points','head_bytes'):
                    assert old[0][key]==new[0][key]
                def returned(rows):
                    return sorted((str(r['instrument_id']),json.dumps(r['payload'],sort_keys=True))
                        for r in rows if r['payload'] is not None)
                assert returned(old)==returned(new)
                assert new[0]['examined_count']<=min(len(requests),new[0]['accepted_count']+1)
        finally:await engine.dispose()
    asyncio.run(run())

def test_sql_never_costs_discarded_suffix():
    async def run():
        engine=create_async_engine(URL,connect_args={'timeout':8,'command_timeout':4})
        try:
            async with engine.connect() as db:
                await db.execute(text('SET TRANSACTION READ ONLY'))
                paths,requests=data()
                requests=[{**requests[0],'n':1,'b':NOW.isoformat()},
                    {**requests[2],'n':2},{**requests[4],'n':3}]
                # The third request's payload cannot be costed. The reader must
                # stop after rejecting the second, before touching that suffix.
                for p in paths:
                    if p['instrument_id']==INSTRUMENT and p['bucket_start']>=(NOW+timedelta(minutes=6)).isoformat():
                        p['payload']['points']='discarded sentinel'
                params={'u':UUID(OWNER),'requests':json.dumps(requests),'count':3,'points':1,'bytes':100000,
                    'fixture_paths':json.dumps(paths)}
                rows=list((await db.execute(text(fixture_sql(FIFO_PREFIX_SQL)),params)).mappings())
                assert rows[0]['accepted_count']==1 and rows[0]['examined_count']==2
                assert len(rows)==2
        finally:await engine.dispose()
    asyncio.run(run())
