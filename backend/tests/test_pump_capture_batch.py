"""Capture transaction: immutable conflicts, one insert per bounded batch."""
import asyncio
import json
import pytest
from test_pump_opportunity import build, OWNER
from app import database
from app.services import pump_opportunity_engine as eng, pump_opportunity_service as svc


@pytest.mark.parametrize("locked", [False, True])
def test_capture_lock_and_immutable_batch_conflicts(monkeypatch, locked):
    p=build();calls=[]
    class Result:
        rowcount=1
        def scalar(self):return locked
        def scalars(self):return self
        def all(self):return []
    class DB:
        async def execute(self,sql,params=None):
            calls.append((str(sql),params));return Result()
    async def run(fn,**kwargs):return await fn(DB())
    async def config(db,user):return eng.config({'enabled':True})
    async def storage(db):return 0
    monkeypatch.setattr(database,'run_db_task',run)
    monkeypatch.setattr(svc,'get_config',config)
    monkeypatch.setattr(svc,'storage_bytes',storage)
    monkeypatch.setattr(eng,'build_observation',lambda **kwargs:dict(p))
    result=asyncio.run(svc.ingest(OWNER,[{'symbol':'BTC_USDT'},{'symbol':'ETH_USDT'}],{}, {}, {}))
    inserts=[(sql,params) for sql,params in calls if 'INSERT INTO pump_opportunity_observations' in sql]
    if not locked:
        assert result['status']=='busy' and not inserts
        assert not any('label_queue' in sql for sql,_ in calls)
    else:
        assert len(inserts)==1
        assert len(json.loads(inserts[0][1]['batch']))==2
        assert 'ON CONFLICT(user_id,instrument_id,slot_at) DO NOTHING' in inserts[0][0]
        assert result['written']==1 and result['duplicates']==1
        assert any('pump_opportunity_label_queue' in sql for sql,_ in calls)
