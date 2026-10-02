import copy
import asyncio,json
from contextlib import asynccontextmanager
from datetime import datetime,timezone
from test_pump_intelligence import row
from app.services import pump_intelligence as agg,pump_executive_insights as insights

def test_seven_hits_eight_unknown_never_become_operational_success_rate():
    sample=[row(True,False) for _ in range(7)]+[row(None,False) for _ in range(8)]
    c=agg.summarize(sample,5,[0,10,20,30,40])[0]
    assert c['baseline']['targets']['0.8']['hits']==7
    assert c['baseline']['targets']['0.8']['known']==7
    assert c['baseline']['targets']['0.8']['unknown']==8
    assert c['baseline']['coverage']['complete']==0
    assert c['executive']['actionable_hour_ranking'] is False
    assert c['executive']['status']=='exploratory_insufficient_for_rules'

def test_hours_use_observation_start_fixed_minus_three_and_local_days():
    a=row();a['payload']['decision_at']='2026-01-01T02:59:00+00:00'
    b=row();b['payload']['decision_at']='2026-01-01T03:00:00+00:00'
    e=agg.summarize([a,b],5,[0,10])[0]['executive']
    assert [g['hour'] for g in e['groups'] if g['kind']=='hour']==[0,23]
    assert e['local_days']==2 and e['hour_basis']=='observation_decision_at'

def test_indicator_ranges_do_not_select_by_outcome_or_fill_missing():
    sample=[row(hit=v) for v in (True,False,None)]
    for r,value in zip(sample,[10,90,None]):r['payload']['values']['rsi']=value
    before=copy.deepcopy(sample);a=agg.summarize(sample,5,[0,10])[0]['executive']
    swapped=copy.deepcopy(sample)
    for r in swapped:r['label']['targets']['0.8']['hit']=False
    b=agg.summarize(swapped,5,[0,10])[0]['executive']
    ranges=lambda e:[(g['condition'],g.get('observed_range'),g.get('condition_rule')) for g in e['groups'] if g['kind']=='indicator']
    assert ranges(a)==ranges(b)
    assert next(x for x in a['indicator_availability'] if x['field']=='rsi')=={'field':'rsi','available':2,'missing':1}
    assert sum(g['support']['observations'] for g in a['groups'] if g.get('field')=='rsi')==2
    assert sample==before and a['flow_semantics']=='aggressor_activity_not_net_capital_inflow'

def test_executive_recuts_never_merge_contracts_or_producers():
    sample=[row(),row()];sample[1]['payload']['manifest']['legacy_config_hash']='other'
    result=agg.summarize(sample,5,[0,10])
    assert len(result)==2
    assert all(c['executive']['groups'][0]['support']['observations']==1 for c in result)

def shadow(outcome='SL_HIT'):
    r={k:'frozen' for k in insights.SHADOW_KEYS}
    r.update(source='L3',direction='SPOT',lineage_status='EXACT',entry_timestamp='2026-01-01T02:00:00Z',
        entry_price=100,sl_price=95,sl_pct=5,tp_pct=1.5,timeout_candles=10,event_id='event',symbol='TEST_USDT',
        status='COMPLETED',outcome=outcome,exit_timestamp='2026-01-01T02:05:00Z',
        label_resolved_at='2026-01-01T02:06:00Z',exit_price_semantics='CLOSED_OHLCV_1M_FIRST_TOUCH_NOMINAL',
        barrier_touched='SL' if outcome=='SL_HIT' else 'TP',barrier_touched_at='2026-01-01T02:05:00Z')
    return r

def test_shadow_sl_support_retains_open_and_unverified_outcomes():
    sample=[shadow(),shadow('TP_HIT'),shadow()]
    sample[2].update(status='RUNNING',outcome=None,exit_timestamp=None)
    result=insights.summarize_shadow(sample);base=result['cohorts'][0]['baseline']
    assert (base['sl_hits'],base['known'],base['unknown'],base['pending'])==(1,2,1,1)
    assert result['cohorts'][0]['hours'][0]['hour']==23
    assert result['association']=='no_verified_pump_shadow_link' and result['real_profit'] is False
    assert result['actionable_sl_recommendation'] is False

def test_shadow_invalid_lineage_not_repaired_and_profiles_sources_policies_separate():
    a=shadow();b=shadow();b['profile_version_id']='another';c=shadow();c['source']='L3_LAB'
    d=shadow();d['lineage_status']='INVALID_AUTHORIZATION_CONTRACT'
    e=shadow();e['exit_policy_hash']='different-global-policy'
    result=insights.summarize_shadow([a,b,c,d,e])
    assert len(result['cohorts'])==4 and result['unverified_rows']==1
    assert all(x['baseline']['observations']==1 for x in result['cohorts'])

def test_shadow_sl_needs_recorded_reason_timestamp_and_exit_semantics():
    good=shadow();both=shadow();both['barrier_touched']='BOTH_SAME_CANDLE'
    invalid=shadow();invalid['barrier_touched']='BARRIER_PATH_UNRESOLVED'
    missing=shadow();missing['exit_price_semantics']=None
    no_reason=shadow();no_reason['barrier_touched']=None
    base=insights.summarize_shadow([good,both,invalid,missing,no_reason])['cohorts'][0]['baseline']
    assert (base['sl_hits'],base['known'],base['unknown'])==(2,2,3)
    assert base['reasons']=={'SL':1,'BOTH_SAME_CANDLE':1}

def test_shadow_missing_contract_is_not_a_zero_sl_cohort():
    r=shadow();r['profile_version_id']=None
    result=insights.summarize_shadow([r]);assert result['cohorts']==[] and result['unverified_rows']==1

def test_shadow_reader_enforces_shared_byte_prefix_and_no_budget_means_no_query():
    from app.services.pump_shadow_context import read_shadow_context,SHADOW_SQL
    document=json.dumps(shadow());calls=[]
    class Result:
        def __init__(self,rows):self.rows=rows
        def mappings(self):return self
        def __iter__(self):return iter(self.rows)
    class DB:
        async def scalar(self,sql):return '0'
        @asynccontextmanager
        async def begin_nested(self):yield
        async def execute(self,sql,params=None):
            if str(sql)==SHADOW_SQL:
                calls.append(params)
                return Result([{'source_text':None,'requested_rows':1}]+(
                    [{'source_text':document,'requested_rows':1}] if params['bytes']>=len(document.encode()) else []))
            return Result([])
    cfg={'read_timeout_ms':2000,'window_hours':24,'temporal_buckets':10,'sample_limit':50}
    async def run():
        db=DB();now=datetime(2026,1,1,tzinfo=timezone.utc)
        empty=await read_shadow_context(db,'owner',cfg,now,0);assert not calls and empty['status']=='unavailable'
        blocked=await read_shadow_context(db,'owner',cfg,now,len(document.encode())-1)
        assert blocked['reason']=='shared_read_byte_budget' and blocked['read_bytes']==0
        good=await read_shadow_context(db,'owner',cfg,now,len(document.encode()))
        assert good['cohorts'][0]['baseline']['sl_hits']==1 and good['read_bytes']==len(document.encode())
        assert all(c['per_bucket']==5 for c in calls)
    asyncio.run(run())
