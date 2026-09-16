from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import pytest
from test_shadow_l3_continuation import policy, ev, T
from app.ml.l3_managed_exit import VERSION, KEY, definition, freeze, certify, digest, lane_config
from app.services.shadow_l3_exit_evaluator import advance
from app.schemas.shadow_l3_exit_policy import frozen_policy


def scenario(policy, kind='TRAILING_STOP'):
    policy = policy.model_copy(update={'mode':'APPLY'})
    config = {KEY:dict(version=VERSION,policy_hash=policy.digest(),trailing_hash=digest({}),fee_roundtrip_pct=.2,slippage_roundtrip_pct=.1,max_holding_seconds=86400,barrier_contract_version='shadow_atr_dynamic_v3')}
    snapshot = dict(shadow_l3_exit_policy=frozen_policy(policy.model_dump()),trailing={},barrier_contract_version='shadow_atr_dynamic_v3',ml_fee_roundtrip_pct=.2)
    snapshot['l3_managed_ml'] = freeze(snapshot,config,source='L3',capture_valid=True)
    # Two known flow signals authorize continuation, then a prior floor is hit.
    prices = [(100,101,100,101),(101,101,101,101),(101,104,101,104),(103,104,100,102)]
    if kind=='TP_HIT': prices=[(100,101,100,101),(101,103,101,102)]
    if kind=='SL_HIT': prices=[(100,101,100,101),(94,96,93,95)]
    if kind=='TIMEOUT': prices=[(100,101,100,101),(101,101,100,101),(101,101,100,101)]
    if kind=='FLOW_STRUCTURE_EXIT': prices=[(100,101,100,101),(101,101,101,101),(101,104,101,104),(106,106,106,106),(106,106,106,106),(106,107,106,107)]
    rows=[];state={}
    for i,(op,hi,lo,close) in enumerate(prices):
        stamp=T+timedelta(minutes=i)
        candle=dict(time=stamp,open=op,high=hi,low=lo,close=close,ingested_at=stamp+timedelta(minutes=1))
        evidence=ev(stamp,weak=kind=='FLOW_STRUCTURE_EXIT' and i in (3,4),lost=True)
        evidence['candle']=candle
        state=advance(state,candle,evidence,policy,entry=100,tp=102,sl=95,trailing={},entry_at=T,timeout_candles=2)
        rows.append(dict(candle_at=stamp,available_at=stamp+timedelta(minutes=1),policy_hash=policy.digest(),evidence=evidence,state=deepcopy(state)))
    assert state['outcome']==kind
    shadow=SimpleNamespace(config_snapshot=snapshot,label_contract_version=VERSION,lineage_status='EXACT',source='L3',
        entry_timestamp=T,exit_timestamp=state['exit_at'],outcome=state['outcome'],closure_path='l3_continuation',
        entry_price=100,tp_price=102,sl_price=95,timeout_candles=2,exit_price=state['exit_price'],
        exit_price_semantics=state['semantics'],pnl_pct=state['exit_price']-100)
    return shadow,rows,config


@pytest.mark.parametrize('kind',['TP_HIT','SL_HIT','TIMEOUT','TRAILING_STOP','FLOW_STRUCTURE_EXIT'])
def test_managed_label_replays_actual_policy_and_costs(policy,kind):
    shadow,rows,cfg=scenario(policy,kind)
    proof=certify(shadow,rows,None,checked_at=T+timedelta(days=2))
    assert proof['valid'],proof
    assert proof['net_return_pct']==pytest.approx(shadow.pnl_pct-.3)
    assert proof['label']==int(shadow.pnl_pct-.3>0)
    assert shadow.config_snapshot['l3_managed_ml']['contract']==definition(cfg)


@pytest.mark.parametrize('mutation,reason',[
    ('gap','PRICE_HISTORY_GAP'),('tamper','REPLAY_STATE_MISMATCH'),('future','INVALID_DECISION_AVAILABILITY'),
    ('legacy','LABEL_CONTRACT_MISMATCH'),('quality','INCOMPLETE_FLOW_EVIDENCE'),('capture','INVALID_CAPTURE'),
    ('return','RETURN_MISMATCH'),('closure','UNSUPPORTED_CLOSURE'),('horizon','CENSORED_OR_INVALID_HORIZON')])
def test_managed_evidence_fails_closed(policy,mutation,reason):
    shadow,rows,cfg=scenario(policy)
    if mutation=='gap':rows.pop(1)
    if mutation=='tamper':rows[1]['state']['high_water_mark']=200
    if mutation=='future':rows[0]['available_at']=T+timedelta(days=3)
    if mutation=='legacy':shadow.label_contract_version='legacy'
    if mutation=='quality':rows[2]['evidence']['quality']='INCOMPLETE_OR_STALE'
    if mutation=='capture':shadow.config_snapshot['l3_managed_ml']['capture_valid']=False
    if mutation=='return':shadow.pnl_pct=100
    if mutation=='closure':shadow.closure_path='legacy'
    if mutation=='horizon':shadow.exit_timestamp=T+timedelta(days=3)
    proof=certify(shadow,rows,None,checked_at=T+timedelta(days=2))
    assert not proof['valid'] and proof['reason']==reason,proof


def test_partial_entry_cannot_hide_barrier_touch(policy):
    shadow,rows,cfg=scenario(policy)
    shadow.entry_timestamp=T+timedelta(seconds=3)
    proof=certify(shadow,rows,dict(time=T,high=104,low=100),checked_at=T+timedelta(days=2))
    assert proof['reason']=='ENTRY_BOUNDARY_AMBIGUOUS'


def test_new_contract_does_not_change_other_lanes_or_old_snapshots(policy):
    shadow,rows,cfg=scenario(policy)
    assert freeze(shadow.config_snapshot,cfg,source='L1_SPECTRUM',capture_valid=True) is None
    assert freeze(shadow.config_snapshot,{},source='L3',capture_valid=True) is None
    old=deepcopy(cfg)
    assert lane_config(cfg)['ml_label_version']==VERSION
    assert cfg==old
    cfg[KEY]['slippage_roundtrip_pct']=float('nan')
    with pytest.raises(ValueError):definition(cfg)


def test_settings_preserve_contract_and_reject_unspecified_cost(policy):
    from app.schemas.strategy_settings import MLShadowConfig
    from app.services.strategy_settings_service import _ml_shadow_dump
    _, _, cfg = scenario(policy)
    assert _ml_shadow_dump(MLShadowConfig.model_validate(cfg))[KEY] == cfg[KEY]
    assert KEY not in _ml_shadow_dump(MLShadowConfig())
    cfg[KEY]['slippage_roundtrip_pct'] = None
    with pytest.raises(ValueError):
        MLShadowConfig.model_validate(cfg)


def test_policy_or_trailing_changes_cannot_join_same_cohort(policy):
    shadow,rows,cfg=scenario(policy)
    shadow.config_snapshot['trailing']={'enabled':True}
    assert freeze(shadow.config_snapshot,cfg,source='L3',capture_valid=True)['capture_valid'] is False
    assert certify(shadow,rows,None,checked_at=T+timedelta(days=2))['reason']=='FROZEN_POLICY_MISMATCH'


@pytest.mark.asyncio
async def test_managed_loader_population_is_segregated_and_mature(policy):
    from app.services.ml_challenger_service import MLChallengerService
    from unittest.mock import AsyncMock, MagicMock
    _,_,cfg=scenario(policy)
    result=MagicMock();result.fetchall.return_value=[]
    db=SimpleNamespace(execute=AsyncMock(return_value=result))
    await MLChallengerService()._load_shadow_data(db,'user',30,['L3'],dataset_valid_from=T,
        dataset_query_cutoff=T+timedelta(days=2),maturity_embargo_margin_minutes=60,managed_contract=definition(cfg))
    sql=str(db.execute.call_args.args[0]);params=db.execute.call_args.args[1]
    for invariant in ["label_contract_version=:managed_version","'TRAILING_STOP'","'FLOW_STRUCTURE_EXIT'","smr.status = 'READY'",'GREATEST(entry_timestamp',"'ml_label'->>'valid'='true'",'completed_at <= :dataset_query_cutoff']:
        assert invariant in sql
    assert params['managed_hash']==definition(cfg)['hash']
    assert params['managed_horizon']==86400


@pytest.mark.asyncio
async def test_legacy_loader_explicitly_excludes_managed_rows():
    from app.services.ml_challenger_service import MLChallengerService
    from unittest.mock import AsyncMock, MagicMock
    result=MagicMock();result.fetchall.return_value=[]
    db=SimpleNamespace(execute=AsyncMock(return_value=result))
    await MLChallengerService()._load_shadow_data(db,'user',30,['L3'],dataset_valid_from=T,
        dataset_query_cutoff=T+timedelta(days=2),maturity_embargo_margin_minutes=60)
    assert "AND NOT (COALESCE(config_snapshot, '{}'::jsonb) ? 'l3_managed_ml')" in str(db.execute.call_args.args[0])


@pytest.mark.asyncio
async def test_inference_rejects_old_artifact_under_new_contract(policy):
    from app.ml.l3_managed_exit import inference_compatible
    from unittest.mock import AsyncMock, MagicMock
    _,_,cfg=scenario(policy)
    result=MagicMock();result.mappings.return_value.all.return_value=[dict(config_type='ml',config_json=cfg)]
    db=SimpleNamespace(execute=AsyncMock(return_value=result))
    assert not await inference_compatible(db,'model',None)


def test_diagnostic_reports_pending_managed_capture_not_fake_exclusion(policy):
    from app.services.l3_capture_diagnostics import capture_stage
    shadow,_,cfg=scenario(policy)
    row=dict(id='new',config_snapshot=shadow.config_snapshot,entry_timestamp=T,outcome=None)
    result=capture_stage(row,cutoff=T+timedelta(minutes=1),config=cfg,eligible_ids=set())
    assert result['stage']=='AWAITING_OUTCOME'
    result=capture_stage(row,cutoff=T+timedelta(days=2),config=cfg,eligible_ids=set())
    assert result['stage']=='EXCLUDED'
