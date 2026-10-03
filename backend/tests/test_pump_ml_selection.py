"""Selection ordering, snapshot and bounded payload regression tests."""
import asyncio,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
from contextlib import asynccontextmanager
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from pump_ml.selection import select_training_rows,IDS_SQL,CONTRACT_SQL,ROWS_SQL

class Cursor:
    def __init__(self,ids):self.ids=list(ids)
    async def fetch(self,n):
        result=self.ids[:n];self.ids=self.ids[n:]
        return [{'observation_id':i} for i in result]

class DB:
    def __init__(self,ids,eligible,certified):
        self.ids=ids;self.eligible=eligible;self.certified=certified;self.calls=[];self.transaction_open=False
    @asynccontextmanager
    async def transaction(self,**kwargs):
        assert kwargs=={'isolation':'repeatable_read','readonly':True}
        self.transaction_open=True
        try:yield
        finally:self.transaction_open=False
    async def cursor(self,sql,*args):
        assert self.transaction_open and sql==IDS_SQL
        self.calls.append(('cursor',args));return Cursor(self.ids)
    async def fetchval(self,sql,*args):
        if sql=='SELECT now()':return datetime(2026,1,31,tzinfo=timezone.utc)
        assert sql==CONTRACT_SQL
        hits=[i for i in args[1] if i in self.certified]
        return {'legacy_config_hash':'producer','label_spec_hash':'label'} if hits else None
    async def fetch(self,sql,*args):
        assert self.transaction_open and sql==ROWS_SQL
        ids,limit=args[-2:];self.calls.append(('payload',ids,limit))
        return [{'row':{'observation_id':i}} for i in ids if i in self.eligible][:limit]

@pytest.mark.parametrize('batch',[1,2,4,50])
def test_batches_preserve_latest_eligible_not_latest_raw_selection(batch):
    # IDs are in descending decision_at then ascending UUID order, including ties.
    db=DB(list(range(12)),{2,5,8,9,11},{4})
    diagnostics={}
    contract,rows=asyncio.run(select_training_rows(db,'owner',['rsi'],'feature',3,diagnostics,batch_size=batch))
    assert [r['observation_id'] for r in rows]==[2,5,8]
    assert contract['label_spec_hash']=='label' and diagnostics['selected_rows']==3
    assert not db.transaction_open
    payloads=[c for c in db.calls if c[0]=='payload']
    assert all(len(c[1])<=batch for c in payloads)
    assert all(c[2]<=3 for c in payloads)
    assert db.calls[0][1][1] is None  # latest contract remains all-time
    dataset_cursor=[c for c in db.calls if c[0]=='cursor'][1]
    assert dataset_cursor[1][1]==datetime(2026,1,1,tzinfo=timezone.utc)

def test_no_certified_contract_never_reads_dataset_payloads():
    db=DB([1,2,3],{1,2,3},set())
    assert asyncio.run(select_training_rows(db,'owner',[],'hash',5,{},batch_size=2))==(None,[])
    assert not any(c[0]=='payload' for c in db.calls)

def test_exhaustion_returns_all_eligible_without_unknown_padding():
    db=DB([1,2,3,4],{2},{1})
    _,rows=asyncio.run(select_training_rows(db,'owner',[],'hash',10,{},batch_size=2))
    assert rows==[{'observation_id':2}]

def test_cancelled_query_retains_phase_and_closes_snapshot():
    db=DB([1,2],{1},{1})
    async def failed(*args):raise RuntimeError('query cancelled')
    db.fetch=failed;diagnostics={}
    with pytest.raises(RuntimeError):asyncio.run(select_training_rows(db,'owner',[],'hash',2,diagnostics))
    assert diagnostics['phase']=='dataset_rows' and not db.transaction_open

@pytest.mark.parametrize('max_rows,batch',[(0,5),(5,0),(-1,1)])
def test_invalid_read_bounds_fail_before_database_access(max_rows,batch):
    with pytest.raises(ValueError):asyncio.run(select_training_rows(None,'owner',[],'hash',max_rows,{},batch_size=batch))

def test_sql_preserves_contract_label_and_temporal_semantics():
    assert 'payload' not in IDS_SQL and 'ORDER BY decision_at DESC,observation_id' in IDS_SQL
    assert 'horizon_minutes=5' in ROWS_SQL and 'label_spec_hash=$5' in ROWS_SQL
    assert "IN('true','false')" in ROWS_SQL and "coverage_complete'='true'" in ROWS_SQL
    assert "feature_spec_hash'=$3" in ROWS_SQL and "legacy_config_hash'=$4" in ROWS_SQL
    assert 'observation_id=ANY($6::uuid[])' in ROWS_SQL

def test_short_selected_history_does_not_silently_reduce_temporal_embargo():
    from app.services.pump_opportunity_engine import purged_split
    from pump_ml.job import prepare,FEATURES
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    rows=[{'decision_at':(start+timedelta(minutes=i)).isoformat(),'observation_id':str(i),
        'episode_id':str(i),'manifest':{'legacy_config_hash':'producer','label_spec_hash':'label','cost_policy_hash':'cost'},
        'label_spec':{'cost_policy':None}} for i in range(400)]
    spec=prepare(rows)
    assert spec['embargo_seconds']==7200 and spec['max_rows']==10000
    rest=rows;cohorts=[]
    for cut in spec['boundaries']:
        before,rest=purged_split(rest,cut,spec['embargo_seconds']);cohorts.append(before)
    cohorts.append(rest)
    assert any(not cohort for cohort in cohorts)
