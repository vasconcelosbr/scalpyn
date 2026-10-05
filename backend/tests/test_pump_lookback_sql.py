"""SELECT-only fixture: minute prefilter preserves exact decision boundaries."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

URL=os.environ.get('PUMP_READER_TEST_DATABASE_URL')

@pytest.mark.skipif(not URL,reason='SELECT-only PostgreSQL fixture URL not configured')
@pytest.mark.parametrize('seconds',[0,1,30,59,59.999999])
def test_minute_prefilter_keeps_inclusive_decision_cutoff(seconds):
    async def run():
        engine=create_async_engine(URL,connect_args={'timeout':8,'command_timeout':4})
        try:
            async with engine.connect() as db:
                await db.execute(text('SET TRANSACTION READ ONLY'))
                at=datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=seconds)
                source="""WITH observations AS (
                    SELECT decision_at,date_trunc('minute',decision_at) slot_at
                    FROM unnest(CAST(:times AS timestamptz[])) decision_at)
                    SELECT decision_at FROM observations WHERE decision_at>=:since"""
                params={'since':at,'times':[at-timedelta(microseconds=1),at,at+timedelta(microseconds=1),at+timedelta(minutes=1)]}
                old=(await db.execute(text(source+' ORDER BY decision_at'),params)).scalars().all()
                new=(await db.execute(text(source+" AND slot_at>=date_trunc('minute',CAST(:since AS timestamptz)) ORDER BY decision_at"),params)).scalars().all()
                assert old==new==params['times'][1:]
        finally:await engine.dispose()
    asyncio.run(run())
