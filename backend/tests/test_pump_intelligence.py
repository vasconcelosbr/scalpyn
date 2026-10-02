import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import datetime,timezone
import pytest
from app.services import pump_opportunity_engine as eng,pump_opportunity_service as svc
from app.services import pump_intelligence as agg,pump_intelligence_reader as reader

NOW=datetime(2026,10,2,12,tzinfo=timezone.utc)

def row(hit=True,complete=True,version='v4',score=20):
    target={'hit':hit,'first_touch_censored':False,'time_to_touch_seconds':45,
            'pre_touch':{'status':'known','drawdown_before_touch_pct':0.2,'mae_before_touch_pct':-0.2,'order_ambiguous':False}}
    return {'payload':{'episode_id':'episode','instrument_id':'instrument','decision_at':NOW.isoformat(),
        'manifest':{'score_config_hash':'score','label_spec_hash':version,'feature_spec_hash':'feature','legacy_config_hash':'producer','cost_policy_hash':'cost'},
        'label_version':version,'reference_policy':'gate_best_ask_v1','score_final':score,
        'values':{'rsi':70},'ledger':[{'group':'flow','result':True},{'group':'structure','result':False}]},
        'label':{'status':'known','coverage_complete':complete,'missing_minutes':0 if complete else 1,
                 'targets':{'0.6':copy.deepcopy(target),'0.8':copy.deepcopy(target)}}}

def test_unknown_pending_and_partial_hits_have_separate_denominators():
    rows=[row(),row(False),row(None,False),row(True,False),row()];rows[-1]['label']=None
    s=agg.describe(rows,5);t=s['targets']['0.8']
    assert (t['hits'],t['misses'],t['known'],t['unknown'])==(2,1,3,2)
    assert t['descriptive_hit_rate']==2/3 and (t['complete_hits'],t['complete_known'])==(1,2)
    assert s['coverage']=={'complete':2,'pending':1,'incomplete':2,'with_gaps':2,'boundary_ambiguous':0,'order_ambiguous':0}
    assert s['episodes']==1 and s['observations']==5 and s['probability_validated'] is False

def test_each_target_uses_its_own_hit_time_and_pre_touch_support():
    r=row();r['label']['targets']['0.6']['time_to_touch_seconds']=10
    r['label']['targets']['0.6']['pre_touch']['drawdown_before_touch_pct']=0.1
    r['label']['targets']['0.8']['pre_touch']['status']='unknown'
    s=agg.describe([r],5)['targets']
    assert s['0.6']['time_to_touch_seconds']['median']==10
    assert s['0.8']['time_to_touch_seconds']['median']==45
    assert s['0.6']['drawdown_before_touch_pct']['known']==1
    assert s['0.8']['drawdown_before_touch_pct']['known']==0

def test_censored_time_and_ambiguous_pre_touch_are_never_exact_measurements():
    r=row();t=r['label']['targets']['0.8'];t['first_touch_censored']=True;t['pre_touch']['order_ambiguous']=True
    s=agg.describe([r],5)['targets']['0.8']
    assert s['hits']==1 and s['first_touch_censored']==1
    assert s['time_to_touch_seconds']['known']==s['drawdown_before_touch_pct']['known']==0

def test_bar_intervals_are_not_midpoints_or_exact_times():
    r=row();t=r['label']['targets']['0.8'];t.pop('time_to_touch_seconds');t['first_touch_interval']=['2026-10-02T12:01:00+00:00','2026-10-02T12:02:00+00:00']
    s=agg.describe([r],5)['targets']['0.8']
    assert s['time_to_touch_seconds']['known']==0
    assert s['time_interval_lower_seconds']['median']==60 and s['time_interval_upper_seconds']['median']==120

def test_contracts_producers_and_score_bands_never_merge():
    a=row(version='v3');b=row();c=row();c['payload']['manifest']['legacy_config_hash']='other'
    rows=[a,b,c];before=copy.deepcopy(rows)
    result=agg.summarize(rows,5,[0,10,20,30,40],[{'field':'rsi','op':'gt','value':65}])
    assert len(result)==3 and len({c['cohort_id'] for c in result})==3
    assert all(c['baseline']['observations']==1 and c['patterns'][0]['pattern']=='flow' and c['score_bands'][0]['pattern']=='[20, 30)' for c in result)
    assert all(c['exploration']['observations']==1 for c in result)
    assert rows==before

@pytest.mark.parametrize('patch',[{'sample_limit':1001},{'cache_seconds':1},{'score_edges':[20,10]},{'max_read_bytes':5000000}])
def test_resource_settings_reject_unbounded_reads(patch):
    with pytest.raises(ValueError):eng.config({'intelligence':patch})

def test_cache_single_flight_reuses_rows_and_failure_preserves_previous_snapshot(monkeypatch):
    reader._cache.clear();reader._locks.clear();reader._failures.clear()
    async def config(db,u):return eng.config()
    monkeypatch.setattr(svc,'get_config',config)
    class Result:
        def __init__(self,rows):self.rows=rows
        def mappings(self):return self
        def __iter__(self):return iter(self.rows)
    class DB:
        samples=0;fail=False
        async def scalar(self,sql):return '0'
        @asynccontextmanager
        async def begin_nested(self):yield
        async def execute(self,sql,params=None):
            if str(sql)==reader.SAMPLE_SQL:
                self.samples+=1
                if self.fail:raise ValueError('simulated query failure')
                r=row()
                item={'source_text':json.dumps(r['payload']),'label_text':json.dumps(r['label']),
                      'has_label':True,'labeled_at':NOW,'slot_at':NOW,'observation_id':'id','requested_rows':1}
                return Result([{'source_text':None,'requested_rows':1},item])
            return Result([])
    async def run():
        db=DB()
        first,second=await asyncio.gather(reader.read_intelligence(db,'owner',None,5),reader.read_intelligence(db,'owner',[{'field':'rsi','op':'gt','value':90}],5))
        assert db.samples==1 and first['computed_at']==second['computed_at']
        assert second['cohorts'][0]['exploration']['observations']==0
        next(iter(reader._cache.values()))['tick']-=61;db.fail=True
        stale=await reader.read_intelligence(db,'owner',None,5)
        retry=await reader.read_intelligence(db,'owner',None,5)
        assert stale['freshness']['refresh_failed'] and retry['computed_at']==first['computed_at'] and db.samples==2
        assert stale['model']['delta']==0 and stale['scope']['whole_history'] is False
    asyncio.run(run())
