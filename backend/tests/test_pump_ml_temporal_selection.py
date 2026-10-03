import asyncio,sys,copy
from pathlib import Path
from datetime import datetime,timedelta,timezone
from contextlib import asynccontextmanager
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from pump_ml.selection import (select_temporal_training_rows,temporal_windows,IDS_SQL,CONTRACT_SQL,
    ROWS_SQL,BOUNDED_IDS_SQL,EARLIEST_IDS_SQL)
from app.services.pump_opportunity_engine import utc,purged_split
from pump_ml.job import prepare

START=datetime(2026,1,1,tzinfo=timezone.utc)
def observation(i,valid=True):
    return {'observation_id':f'{i:08d}','decision_at':(START+timedelta(minutes=5*i)).isoformat(),
        'episode_id':str(i),'instrument_id':str(i%15),'values':{'rsi':50 if valid else None},
        'manifest':{'legacy_config_hash':'producer','label_spec_hash':'label','cost_policy_hash':'cost'},
        'label_spec':{'cost_policy':None},'target':i%10==0}

class Cursor:
    def __init__(self,rows):self.rows=rows
    async def fetch(self,n):
        result=self.rows[:n];self.rows=self.rows[n:]
        return [{'observation_id':r['observation_id']} for r in result]
class DB:
    def __init__(self,rows,eligible=None):
        self.rows=rows;self.eligible=eligible;self.open=False;self.calls=[]
    @asynccontextmanager
    async def transaction(self,**kwargs):
        assert kwargs=={'isolation':'repeatable_read','readonly':True}
        self.open=True
        try:yield
        finally:self.open=False
    async def fetchval(self,sql,*args):
        assert self.open
        if sql=='SELECT now()':return START+timedelta(days=10)
        assert sql==CONTRACT_SQL
        return {'legacy_config_hash':'producer','label_spec_hash':'label'}
    async def cursor(self,sql,*args):
        assert self.open;self.calls.append(('ids',sql,args))
        if sql==IDS_SQL:rows=self.rows
        else:
            assert sql in (BOUNDED_IDS_SQL,EARLIEST_IDS_SQL)
            _,lo,hi,inclusive=args
            rows=[r for r in self.rows if utc(r['decision_at'])>=lo and
                (hi is None or utc(r['decision_at'])<hi or inclusive and utc(r['decision_at'])==hi)]
        rows=sorted(rows,key=lambda r:(utc(r['decision_at']),r['observation_id']),reverse=sql!=EARLIEST_IDS_SQL)
        return Cursor(rows)
    async def fetch(self,sql,*args):
        assert self.open and sql==ROWS_SQL
        ids,limit=args[-2:];self.calls.append(('payload',len(ids),limit))
        rows=[r for r in self.rows if r['observation_id'] in ids and
            (self.eligible is None or r['observation_id'] in self.eligible)]
        rows=sorted(rows,key=lambda r:(-utc(r['decision_at']).timestamp(),r['observation_id']))
        return [{'row':copy.deepcopy(r)} for r in rows[:limit]]

def select(rows,limit=200,bins=20,batch=25,eligible=None):
    db=DB(rows,eligible);diagnostics={}
    contract,result=asyncio.run(select_temporal_training_rows(db,'owner',['rsi'],'hash',limit,diagnostics,batch_size=batch,bins=bins))
    assert not db.open
    assert all(c[1]<=batch for c in db.calls if c[0]=='payload')
    return result,diagnostics

@pytest.mark.parametrize('batch',[1,7,25,500])
def test_temporal_sampling_spans_history_and_io_batch_does_not_change_ids(batch):
    rows=[observation(i) for i in range(1000)]
    result,d=select(rows,batch=batch)
    baseline,_=select(rows,batch=25)
    assert [r['observation_id'] for r in result]==[r['observation_id'] for r in baseline]
    assert len(result)==200 and len({r['observation_id'] for r in result})==200
    assert d['policy']['actual_bins']==20 and d['policy']['outcome_balancing'] is False
    assert all(w['selected']==w['quota']==10 for w in d['policy']['windows'])
    assert utc(result[-1]['decision_at'])<START+timedelta(hours=5)
    assert utc(result[0]['decision_at'])==utc(rows[-1]['decision_at'])

def test_label_and_score_values_do_not_change_sampling_membership():
    rows=[observation(i) for i in range(1000)]
    reversed_labels=copy.deepcopy(rows)
    for r in reversed_labels:r['target']=not r['target'];r['score_final']=100
    a,_=select(rows);b,_=select(reversed_labels)
    assert [r['observation_id'] for r in a]==[r['observation_id'] for r in b]

def test_feature_invalid_rows_do_not_displace_numeric_zero_or_older_valid_rows():
    rows=[observation(i,i%2==0) for i in range(1000)]
    rows[0]['values']['rsi']=0
    result,d=select(rows,batch=7)
    assert len(result)==200 and all(r['values']['rsi'] is not None for r in result)
    assert d['policy']['first']==START.isoformat()  # numeric zero is eligible

def test_sparse_windows_do_not_backfill_from_dense_newest_tail():
    rows=[observation(i) for i in list(range(20))+list(range(980,1000))]
    result,d=select(rows,limit=100,bins=10)
    assert len(result)<100
    assert any(w['selected']==0 for w in d['policy']['windows'])
    assert all(w['selected']<=w['quota'] for w in d['policy']['windows'])

def test_no_complete_features_keeps_dataset_empty():
    rows=[observation(i,False) for i in range(100)]
    result,d=select(rows)
    assert result==[] and d['selected_rows']==0

def test_window_edges_assign_each_timestamp_once_and_include_last_endpoint():
    windows=temporal_windows(START,START+timedelta(hours=20),203,20)
    assert sum(w[2] for w in windows)==203 and max(w[2] for w in windows)==11
    for t in [START]+[w[1] for w in windows]:
        assert sum(lo<=t and (t<hi or inclusive and t==hi) for lo,hi,_,inclusive in windows)==1
    assert temporal_windows(START,START,10,20)==[(START,START,10,True)]

def test_policy_is_frozen_in_manifest_and_purge_embargo_remain_unchanged():
    rows=[observation(i) for i in range(1000)]
    selected,d=select(rows)
    spec=prepare(selected,d['policy'])
    assert spec['selection_policy']==d['policy'] and spec['embargo_seconds']==7200
    assert spec['max_rows']==10000 and spec['max_threads']==1
    cohorts=[];rest=selected
    for cut in spec['boundaries']:
        before,rest=purged_split(rest,cut,spec['embargo_seconds']);cohorts.append(before)
    cohorts.append(rest)
    assert all(c for c in cohorts)
    sets=[{r['episode_id'] for r in c} for c in cohorts]
    assert all(not a&b for i,a in enumerate(sets) for b in sets[i+1:])
    # Retaining sparse positive classes is a model gate, not a sampler objective.

def test_cancellation_closes_snapshot_and_preserves_failed_phase():
    db=DB([observation(i) for i in range(5)]);d={}
    async def cancelled(*args):raise RuntimeError('cancelled')
    db.fetch=cancelled
    with pytest.raises(RuntimeError):asyncio.run(select_temporal_training_rows(db,'owner',['rsi'],'hash',5,d))
    assert not db.open and d['phase']=='earliest_eligible'
