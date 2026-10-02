"""Exploratory, compatible-cohort descriptions; no ranking or execution consumer."""
from collections import defaultdict
from datetime import timedelta,timezone
from statistics import median
from . import pump_opportunity_engine as eng

GMT_MINUS_3=timezone(timedelta(hours=-3))
INDICATORS=('rsi','adx','rvol_strict','delta_norm','buy_persistence','spread_pct','price_extension_atr')

def executive_groups(sample,horizon,describe):
    """Ranges use observed values, not optimized outcomes or admission thresholds."""
    hours=defaultdict(list)
    for r in sample:
        hours[eng.utc(r['payload']['decision_at']).astimezone(GMT_MINUS_3).hour].append(r)
    groups=[{'kind':'hour','condition':f'{hour:02d}:00–{hour:02d}:59 GMT−3',
        'hour':hour,'support':describe(rows,horizon)} for hour,rows in sorted(hours.items())]
    availability=[]
    for field in INDICATORS:
        available=[r for r in sample if eng.number(r['payload']['values'].get(field))]
        availability.append({'field':field,'available':len(available),'missing':len(sample)-len(available)})
        if not available:continue
        values=[r['payload']['values'][field] for r in available];lo=min(values);hi=max(values);cut=median(values)
        # A constant field offers no split; missing data gets its own exposure.
        slices=[(f'{field} = {lo:g}',available,'eq',lo)] if lo==hi else [
            (f'{field} < {cut:g}',[r for r in available if r['payload']['values'][field]<cut],'lt',cut),
            (f'{field} ≥ {cut:g}',[r for r in available if r['payload']['values'][field]>=cut],'gte',cut)]
        for condition,rows,op,value in slices:
            if rows:groups.append({'kind':'indicator','field':field,'condition':condition,
                'observed_range':[lo,hi],'split':cut,'range_policy':'sample_median_no_outcome_selection',
                'condition_rule':{'field':field,'op':op,'value':value},
                'support':describe(rows,horizon)})
    return {'status':'exploratory_insufficient_for_rules','timezone':'UTC-03:00',
        'local_days':len({eng.utc(r['payload']['decision_at']).astimezone(GMT_MINUS_3).date() for r in sample}),
        'hour_basis':'observation_decision_at','actionable_hour_ranking':False,
        'groups':groups,'indicator_availability':availability,
        'tested_groups':len(groups),'overlapping_episodes':len(sample)>len({r['payload']['episode_id'] for r in sample}),
        'flow_semantics':'aggressor_activity_not_net_capital_inflow',
        'suggestion':'Replicar as condições em outro período e em episódios novos do mesmo contrato; comparar cobertura e custos antes de testar mudanças.'}


SHADOW_KEYS=('source','direction','exchange','timeframe','profile_version_id','profile_config_hash',
    'score_engine_version_id','score_engine_config_hash','feature_schema_version','capture_contract_version',
    'label_contract_version','barrier_contract_version','entry_price_contract_version','exit_policy_hash',
    'sl_pct','tp_pct','timeout_candles')
REASONS={'SL_HIT':('SL','BOTH_SAME_CANDLE'),'TP_HIT':('TP',),'TRAILING_STOP':('TRAILING',),'TIMEOUT':('NONE',)}

def verified_shadow_outcome(r):
    outcome=r.get('outcome')
    return bool(r.get('status')=='COMPLETED' and r.get('exit_timestamp') and r.get('label_resolved_at')
        and r.get('exit_price_semantics')=='CLOSED_OHLCV_1M_FIRST_TOUCH_NOMINAL'
        and r.get('barrier_touched') in REASONS.get(outcome,())
        and (outcome=='TIMEOUT' or r.get('barrier_touched_at')))

def shadow_support(rows):
    known=[r for r in rows if verified_shadow_outcome(r)]
    entries=[eng.utc(r['entry_timestamp']).isoformat() for r in rows]
    reasons=defaultdict(int);unknown_reasons=defaultdict(int)
    for r in known:reasons[r['barrier_touched']]+=1
    for r in rows:
        if verified_shadow_outcome(r):continue
        reason=('open' if r.get('status') in ('RUNNING','PENDING') else
            'resolution_timestamp_missing' if not r.get('label_resolved_at') else
            'exit_contract_unverified' if r.get('exit_price_semantics')!='CLOSED_OHLCV_1M_FIRST_TOUCH_NOMINAL' else
            'barrier_evidence_unverified')
        unknown_reasons[reason]+=1
    return {'observations':len(rows),'events':len({r['event_id'] for r in rows if r.get('event_id')}),
        'events_missing':sum(not r.get('event_id') for r in rows),'instruments':len({r['symbol'] for r in rows}),
        'known':len(known),'sl_hits':sum(r['outcome']=='SL_HIT' for r in known),'unknown':len(rows)-len(known),
        'pending':sum(r.get('status') in ('RUNNING','PENDING') for r in rows),
        'coverage':len(known)/len(rows) if rows else None,'reasons':dict(reasons),'unknown_reasons':dict(unknown_reasons),
        'from':min(entries,default=None),'to':max(entries,default=None)}

def summarize_shadow(rows):
    contracts=defaultdict(list);unverified=0
    for r in rows:
        if (r.get('lineage_status')!='EXACT' or r.get('direction')!='SPOT' or not r.get('entry_timestamp')
            or any(r.get(k) is None or r.get(k)=='' for k in SHADOW_KEYS)
            or not eng.number(r.get('entry_price')) or r['entry_price']<=0
            or not eng.number(r.get('sl_price')) or r['sl_price']<=0):
            unverified+=1;continue
        contracts[tuple(str(r[k]) for k in SHADOW_KEYS)].append(r)
    groups=[]
    for key,sample in sorted(contracts.items()):
        hours=defaultdict(list)
        for r in sample:hours[eng.utc(r['entry_timestamp']).astimezone(GMT_MINUS_3).hour].append(r)
        groups.append({'cohort_id':eng.canonical_hash(dict(zip(SHADOW_KEYS,key))),
            'contract':dict(zip(SHADOW_KEYS,key)),'baseline':shadow_support(sample),
            'hours':[{'hour':h,'support':shadow_support(v)} for h,v in sorted(hours.items())]})
    return {'status':'descriptive_separate_simulations','association':'no_verified_pump_shadow_link',
        'timezone':'UTC-03:00','hour_basis':'shadow_entry_timestamp','sampled_rows':len(rows),
        'unverified_rows':unverified,'cohorts':groups,'actionable_sl_recommendation':False,
        'real_profit':False,'whole_history':False}
