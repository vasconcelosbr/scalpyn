from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import pytest
from test_shadow_l3_continuation import policy, ev, T
from app.ml.l3_managed_exit import VERSION, KEY, definition, freeze, certify, digest, lane_config
from app.services.shadow_l3_exit_evaluator import advance
from app.schemas.shadow_l3_exit_policy import frozen_policy


def scenario(policy, kind='TRAILING_STOP', max_holding=86400, invalid=(), timeout_candles=2):
    policy = policy.model_copy(update={'mode':'APPLY'})
    config = {KEY:dict(version=VERSION,policy_hash=policy.digest(),trailing_hash=digest({}),fee_roundtrip_pct=.2,slippage_roundtrip_pct=.1,max_holding_seconds=max_holding,barrier_contract_version='shadow_atr_dynamic_v3')}
    snapshot = dict(shadow_l3_exit_policy=frozen_policy(policy.model_dump()),trailing={},barrier_contract_version='shadow_atr_dynamic_v3',ml_fee_roundtrip_pct=.2)
    snapshot['l3_managed_ml'] = freeze(snapshot,config,source='L3',capture_valid=True)
    # Two known flow signals authorize continuation, then a prior floor is hit.
    prices = [(100,101,100,101),(101,101,101,101),(101,104,101,104),(103,104,100,102)]
    if kind=='TP_HIT': prices=[(100,101,100,101),(101,103,101,102)]
    if kind=='SL_HIT': prices=[(100,101,100,101),(94,96,93,95)]
    if kind=='TIMEOUT': prices=[(100,101,100,101),(101,101,100,101),(101,101,100,101)]
    if kind=='SL_LATE': prices=[(100,101,100,101),(101,101,100,101),(100,101,99,100),(94,96,93,95)]
    if kind=='FLOW_STRUCTURE_EXIT': prices=[(100,101,100,101),(101,101,101,101),(101,104,101,104),(106,106,106,106),(106,106,106,106),(106,107,106,107)]
    rows=[];state={}
    for i,(op,hi,lo,close) in enumerate(prices):
        stamp=T+timedelta(minutes=i)
        candle=dict(time=stamp,open=op,high=hi,low=lo,close=close,ingested_at=stamp+timedelta(minutes=1))
        evidence=ev(stamp,weak=kind=='FLOW_STRUCTURE_EXIT' and i in (3,4),lost=True,valid=i not in invalid)
        evidence['candle']=candle
        state=advance(state,candle,evidence,policy,entry=100,tp=102,sl=95,trailing={},entry_at=T,timeout_candles=timeout_candles)
        rows.append(dict(candle_at=stamp,available_at=stamp+timedelta(minutes=1),policy_hash=policy.digest(),evidence=evidence,state=deepcopy(state)))
    assert state['outcome']==('SL_HIT' if kind=='SL_LATE' else kind)
    shadow=SimpleNamespace(config_snapshot=snapshot,label_contract_version=VERSION,lineage_status='EXACT',source='L3',
        entry_timestamp=T,exit_timestamp=state['exit_at'],outcome=state['outcome'],closure_path='l3_continuation',
        entry_price=100,tp_price=102,sl_price=95,timeout_candles=timeout_candles,exit_price=state['exit_price'],
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


def test_v2_policy_capture_stays_isolated_until_ml_contract_pins_new_hash(policy):
    """S2 (2026-09-17): switching a tenant's live shadow_l3_exit_policy to v2
    must not silently backfill new-version captures into the existing v1 ML
    training population. freeze() already pins the ML economic contract's
    policy_hash; a v2-frozen snapshot whose contract still names the OLD v1
    hash must fail capture_valid, not pass through -- and only becomes
    eligible again once the contract is explicitly re-pinned to the new hash.
    """
    from app.schemas.shadow_l3_exit_policy import ShadowL3ExitPolicyV2
    v1 = policy.model_copy(update={'mode': 'APPLY'})
    v2 = ShadowL3ExitPolicyV2.model_validate({
        **v1.model_dump(), 'version': 'shadow_l3_continuation_v2',
        'flow_window_age_seconds': 120, 'mode': 'APPLY',
    })
    assert v2.digest() != v1.digest()
    config = {KEY: dict(version=VERSION, policy_hash=v1.digest(), trailing_hash=digest({}),
                         fee_roundtrip_pct=.2, slippage_roundtrip_pct=.1,
                         max_holding_seconds=86400, barrier_contract_version='shadow_atr_dynamic_v3')}
    snapshot = dict(shadow_l3_exit_policy=frozen_policy(v2.model_dump()), trailing={},
                     barrier_contract_version='shadow_atr_dynamic_v3', ml_fee_roundtrip_pct=.2)
    result = freeze(snapshot, config, source='L3', capture_valid=True)
    assert result['capture_valid'] is False
    assert result['capture_reason'] == 'MANAGED_EXIT_CONFIG_MISMATCH'

    config[KEY]['policy_hash'] = v2.digest()
    result2 = freeze(snapshot, config, source='L3', capture_valid=True)
    assert result2['capture_valid'] is True


def test_hash_is_stable_across_int_and_float_numeric_representation(policy):
    """A settings-form save that round-trips the whole ml config through JS
    (no int/float distinction) can turn 0.0 into 0 on a field the user never
    touched. That must not flip the contract hash -- it flipped it for real
    on 2026-09-20, orphaning every shadow trade captured before the save from
    the L3_PROFILE training population despite an unchanged economic
    contract (0% slippage either way).
    """
    base = dict(version=VERSION, policy_hash=policy.digest(), trailing_hash=digest({}),
                barrier_contract_version='shadow_atr_dynamic_v3')
    as_float = definition({KEY: {**base, 'fee_roundtrip_pct': .2, 'slippage_roundtrip_pct': 0.0,
                                  'max_holding_seconds': 86400}})
    as_int = definition({KEY: {**base, 'fee_roundtrip_pct': .2, 'slippage_roundtrip_pct': 0,
                                'max_holding_seconds': 86400}})
    assert as_float['hash'] == as_int['hash']


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
    for invariant in ["label_contract_version=:managed_version","'TRAILING_STOP'","'FLOW_STRUCTURE_EXIT'","smr.status = 'READY'",
                       'label_resolved_at + make_interval','completed_at <= :dataset_query_cutoff']:
        assert invariant in sql
    # An early closer (TP/trailing-stop) is certified the moment it closes;
    # maturity must key off that resolution time, not off the position's max
    # allowed duration (managed_horizon still gates holding_seconds <=, a
    # separate correctness check, not a per-row maturity wait).
    assert 'GREATEST(entry_timestamp' not in sql
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


def test_diagnostic_reports_every_concurrent_impediment_not_just_first(policy):
    """S0.6 (2026-09-17 shadow-trade collapse fix): a closed managed-exit
    capture like UNI_USDT/b13d595f (invalid flow evidence + no measurement
    + not yet mature, all true at once) must surface all three, not just
    whichever one the code happened to check first.
    """
    from app.services.l3_capture_diagnostics import capture_stage
    shadow, _, cfg = scenario(policy)
    cfg['ml_maturity_embargo_margin_minutes'] = 60
    row = dict(
        id='uni', config_snapshot=shadow.config_snapshot, entry_timestamp=T,
        outcome='TRAILING_STOP', measurement_status=None, entry_quality=None,
        managed_label={'valid': False, 'reason': 'INCOMPLETE_FLOW_EVIDENCE'},
    )
    result = capture_stage(row, cutoff=T + timedelta(minutes=1), config=cfg, eligible_ids=set())
    codes = {item['code'] for item in result['impediments']}
    assert codes == {'INCOMPLETE_FLOW_EVIDENCE', 'MEASUREMENT_NOT_READY', 'AWAITING_MATURITY'}
    assert len(result['impediments']) == 3
    # Primary reason/stage still reflects the first impediment, for callers
    # that only read the top-level fields.
    assert result['reason'] == 'INCOMPLETE_FLOW_EVIDENCE'
    assert result['stage'] == 'EXCLUDED'


def test_managed_capture_with_no_impediments_is_not_misreported_as_incompatible_outcome(policy):
    """Item 7 of the L3_PROFILE flow-evidence-gate fix (2026-09-18): a managed
    capture that cleared every impediment (valid proof, ready measurement,
    matured) but is not yet reflected in eligible_ids (e.g. a lag on the
    canonical trainer query) used to fall through to a legacy outcome
    allowlist ('TP_HIT','SL_HIT','TIMEOUT') that predates TRAILING_STOP and
    FLOW_STRUCTURE_EXIT as valid managed outcomes -- misreporting it as
    EXCLUDED for an incompatible outcome the managed contract explicitly
    allows (l3_managed_exit.OUTCOMES).
    """
    from app.services.l3_capture_diagnostics import capture_stage
    shadow, _, cfg = scenario(policy, kind='TRAILING_STOP')
    cfg['ml_maturity_embargo_margin_minutes'] = 0
    row = dict(
        id='trailing-capture', config_snapshot=shadow.config_snapshot,
        entry_timestamp=T, outcome='TRAILING_STOP',
        measurement_status='READY', entry_quality='OK',
        managed_label={'valid': True, 'label_available_at': None},
    )
    result = capture_stage(row, cutoff=T + timedelta(days=2), config=cfg, eligible_ids=set())
    assert result['stage'] != 'EXCLUDED', result
    assert 'fora do contrato' not in (result['reason'] or ''), result


def test_early_closer_matures_from_resolution_time_not_full_horizon(policy):
    """User-requested correction (2026-09-19): a position that resolves fast
    (TP/trailing-stop) is certified the moment it closes -- label_available_at
    already reflects that. Flooring maturity at entry+max_holding_seconds (the
    24h contract horizon) made every early closer wait out the full horizon
    regardless of how quickly its label was actually confirmed. Maturity must
    key off label_available_at + embargo alone.
    """
    from app.services.l3_capture_diagnostics import capture_stage
    shadow, rows, cfg = scenario(policy, kind='TP_HIT')
    checked_at = T + timedelta(minutes=90)
    proof = certify(shadow, rows, None, checked_at=checked_at)
    assert proof['valid'], proof
    cfg['ml_maturity_embargo_margin_minutes'] = 60
    row = dict(id='fast', config_snapshot=shadow.config_snapshot, entry_timestamp=T,
               outcome=shadow.outcome, measurement_status='READY', entry_quality='OK',
               managed_label=proof)
    mature_at = checked_at + timedelta(minutes=60)
    just_before = capture_stage(row, cutoff=mature_at - timedelta(minutes=1), config=cfg, eligible_ids=set())
    assert just_before['stage'] == 'AWAITING_MATURITY', just_before
    just_after = capture_stage(row, cutoff=mature_at + timedelta(minutes=1), config=cfg, eligible_ids=set())
    assert just_after['stage'] != 'AWAITING_MATURITY', just_after
    # Nowhere near entry + the 24h max_holding_seconds horizon -- proves the
    # wait no longer floors at the full contract horizon.
    assert mature_at < T + timedelta(hours=24)


@pytest.mark.asyncio
async def test_flow_evidence_detail_names_the_specific_threshold_broken():
    """The generic INCOMPLETE_FLOW_EVIDENCE code hides which policy limit was
    actually violated. _flow_evidence_detail must look up the failing
    evaluation and name the exact metric and threshold, e.g. 'idade do fluxo
    ... de 142.97 s, acima de 120 s' -- not just repeat the code.
    """
    from unittest.mock import AsyncMock, MagicMock
    from app.services.l3_capture_diagnostics import _flow_evidence_detail
    row = dict(
        id='uni', entry_timestamp=T,
        config_snapshot={'shadow_l3_exit_policy': {'config': {
            'warmup_seconds': 60, 'max_gap_seconds': 30,
            'min_coverage_pct': 80, 'max_age_seconds': 120,
        }}},
    )
    failing_row = {
        'candle_at': T + timedelta(minutes=5), 'quality': 'INCOMPLETE_OR_STALE',
        'data_age_seconds': 142.97, 'flow_window_age_seconds': None, 'max_gap_seconds': 10.0,
        'coverage_pct': 95.0, 'collection_lag_seconds': 5.0,
    }
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = failing_row
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    detail = await _flow_evidence_detail(db, row)
    assert detail['data_age_seconds'] == 142.97
    assert 'idade do fluxo' in detail['reason']
    assert '142.97 s' in detail['reason']
    assert 'acima de 120 s' in detail['reason']


@pytest.mark.asyncio
async def test_flow_evidence_detail_names_flow_window_dimension_under_v2():
    """S1 (2026-09-17): under a v2 policy the gate that actually fired is
    flow_window_age_seconds, not the (unused-for-quality) combined
    data_age_seconds -- the diagnostic message must name the real culprit.
    """
    from unittest.mock import AsyncMock, MagicMock
    from app.services.l3_capture_diagnostics import _flow_evidence_detail
    row = dict(
        id='uni-v2', entry_timestamp=T,
        config_snapshot={'shadow_l3_exit_policy': {'config': {
            'warmup_seconds': 60, 'max_gap_seconds': 30, 'min_coverage_pct': 80,
            'max_age_seconds': 120, 'alignment_seconds': 120, 'flow_window_age_seconds': 120,
        }}},
    )
    failing_row = {
        'candle_at': T + timedelta(minutes=5), 'quality': 'INCOMPLETE_OR_STALE',
        'data_age_seconds': 142.97, 'flow_window_age_seconds': 142.97,
        'max_gap_seconds': 10.0, 'coverage_pct': 95.0, 'collection_lag_seconds': 5.0,
    }
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = failing_row
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    detail = await _flow_evidence_detail(db, row)
    assert 'idade do fluxo na janela' in detail['reason']
    assert '142.97 s' in detail['reason']


# --- l3_managed_cert_v2: certification rules versioned apart from the contract ---
from app.ml.l3_managed_exit import certification_rules, CERTIFICATION_V1, CERTIFICATION_V2

V2 = dict(version=CERTIFICATION_V2, flow_evidence='REPLAY_FALLBACK', timeout_horizon='POLICY_TIMEOUT')


def test_v1_output_is_unchanged_when_no_rules_are_passed(policy):
    shadow, rows, _ = scenario(policy)
    proof = certify(shadow, rows, None, checked_at=T+timedelta(days=2))
    assert proof['valid']
    assert not {'certification_version', 'degraded_flow_decisions', 'policy_timeout_accepted'} & set(proof)
    explicit_v1 = dict(version=CERTIFICATION_V1, flow_evidence='REQUIRE_VALID', timeout_horizon='EXIT_AT')
    again = certify(shadow, rows, None, checked_at=T+timedelta(days=2), certification=explicit_v1)
    assert {k: v for k, v in again.items() if k not in ('certification_version', 'degraded_flow_decisions',
            'policy_timeout_accepted')} == proof


def test_policy_timeout_stamped_after_close_is_censored_by_v1_and_certified_by_v2(policy):
    # timeout_candles=2 -> the policy decides TIMEOUT on the candle at +120 s,
    # but DECISION_CLOSE stamps exit_at when that candle's decision is
    # available (+180 s): exactly the production artefact (86 525-86 679 s).
    shadow, rows, _ = scenario(policy, 'TIMEOUT', max_holding=120)
    assert (shadow.exit_timestamp if isinstance(shadow.exit_timestamp, str) else shadow.exit_timestamp.isoformat()) == (T+timedelta(minutes=3)).isoformat()
    assert certify(shadow, rows, None, checked_at=T+timedelta(days=2))['reason'] == 'CENSORED_OR_INVALID_HORIZON'
    proof = certify(shadow, rows, None, checked_at=T+timedelta(days=2), certification=V2)
    assert proof['valid'], proof
    assert proof['policy_timeout_accepted'] is True
    assert proof['net_return_pct'] == pytest.approx(shadow.pnl_pct-.3)


def test_v2_still_censors_timeouts_beyond_the_contract_horizon(policy):
    shadow, rows, _ = scenario(policy, 'TIMEOUT', max_holding=120)
    shadow.timeout_candles = 3  # frozen policy timeout (180 s) exceeds the 120 s contract
    assert certify(shadow, rows, None, checked_at=T+timedelta(days=2), certification=V2)['reason'] == 'CENSORED_OR_INVALID_HORIZON'
    shadow, rows, _ = scenario(policy, 'TRAILING_STOP', max_holding=120)
    # non-timeout outcomes keep the strict exit-stamp horizon under v2
    assert certify(shadow, rows, None, checked_at=T+timedelta(days=2), certification=V2)['reason'] == 'CENSORED_OR_INVALID_HORIZON'


def test_v2_replays_degraded_flow_instead_of_discarding_the_label(policy):
    # Evidence that was not VALID when the shadow ran: the policy could not
    # extend, so the persisted states already reflect the fallback path.
    # The floor touch on the post-warmup candle (+180 s) is decided before
    # flow quality is read, so the degraded minute leaves the exit intact.
    shadow, rows, _ = scenario(policy, 'TRAILING_STOP', invalid=(3,))
    assert rows[3]['evidence']['quality'] != 'VALID'
    v1 = certify(shadow, rows, None, checked_at=T+timedelta(days=2))
    assert v1['reason'] == 'INCOMPLETE_FLOW_EVIDENCE'
    v2 = certify(shadow, rows, None, checked_at=T+timedelta(days=2), certification=V2)
    assert v2['valid'], v2
    assert v2['degraded_flow_decisions'] == 1
    assert v2['label'] == int(shadow.pnl_pct-.3 > 0) and v2['net_return_pct'] == pytest.approx(shadow.pnl_pct-.3)


@pytest.mark.parametrize('mutation,reason', [
    ('gap', 'PRICE_HISTORY_GAP'), ('tamper', 'REPLAY_STATE_MISMATCH'), ('future', 'INVALID_DECISION_AVAILABILITY'),
    ('legacy', 'LABEL_CONTRACT_MISMATCH'), ('capture', 'INVALID_CAPTURE'), ('return', 'RETURN_MISMATCH'),
    ('closure', 'UNSUPPORTED_CLOSURE')])
def test_v2_keeps_every_other_proof_fail_closed(policy, mutation, reason):
    shadow, rows, _ = scenario(policy)
    if mutation == 'gap': rows.pop(1)
    if mutation == 'tamper': rows[1]['state']['high_water_mark'] = 200
    if mutation == 'future': rows[0]['available_at'] = T+timedelta(days=3)
    if mutation == 'legacy': shadow.label_contract_version = 'legacy'
    if mutation == 'capture': shadow.config_snapshot['l3_managed_ml']['capture_valid'] = False
    if mutation == 'return': shadow.pnl_pct = 100
    if mutation == 'closure': shadow.closure_path = 'legacy'
    proof = certify(shadow, rows, None, checked_at=T+timedelta(days=2), certification=V2)
    assert not proof['valid'] and proof['reason'] == reason, proof


def test_v2_degraded_evidence_cannot_be_rewritten_into_a_different_exit(policy):
    # Flipping persisted evidence to invalid after the fact changes the replay
    # (no continuation) and must be caught as a state mismatch, not accepted.
    shadow, rows, _ = scenario(policy, 'TRAILING_STOP')
    rows[2]['evidence']['quality'] = 'INCOMPLETE_OR_STALE'
    proof = certify(shadow, rows, None, checked_at=T+timedelta(days=2), certification=V2)
    assert not proof['valid']


@pytest.mark.parametrize('raw', [
    {}, dict(V2, extra=1), dict(V2, version='x'), dict(V2, flow_evidence='ANY'), dict(V2, timeout_horizon='ANY'),
    dict(version=CERTIFICATION_V1, flow_evidence='REPLAY_FALLBACK', timeout_horizon='EXIT_AT')])
def test_certification_rules_fail_closed(raw):
    with pytest.raises(ValueError):
        certification_rules(raw)


@pytest.mark.asyncio
async def test_override_loader_replaces_only_v1_proof_clauses(policy):
    from app.services.ml_challenger_service import MLChallengerService
    from unittest.mock import AsyncMock, MagicMock
    _, _, cfg = scenario(policy)
    spec = definition(cfg)
    rows = [SimpleNamespace(_mapping=dict(shadow_id=i, entry_timestamp=T+timedelta(hours=1), completed_at=T+timedelta(hours=2),
            net_return_pct=None, label_resolved_at=None)) for i in ('a', 'b', 'c')]
    result = MagicMock(); result.fetchall.return_value = rows
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    service = MLChallengerService()
    overrides = {'a': dict(valid=True, contract_hash=spec['hash'], net_return_pct=-1.5, certification_version=CERTIFICATION_V2),
                 'b': dict(valid=False, reason='PRICE_HISTORY_GAP')}
    records = await service._load_shadow_data(db, 'user', 30, ['L3'], dataset_valid_from=T,
        dataset_query_cutoff=T+timedelta(days=2), maturity_embargo_margin_minutes=60,
        managed_contract=spec, managed_label_overrides=overrides)
    sql = str(db.execute.call_args.args[0])
    assert "id = ANY(CAST(:override_ids AS uuid[]))" in sql
    assert db.execute.call_args.args[1]['override_ids'] == ['a']  # only valid proofs reach SQL
    for kept in ("label_contract_version=:managed_version", "capture_valid'='true'", "smr.status = 'READY'",
                 "lineage_status = 'EXACT'", "closure_path='l3_continuation'", "'TIMEOUT'", "'TRAILING_STOP'"):
        assert kept in sql
    for dropped in ("eligible_for_training IS TRUE", "shadow_l3_exit_states", "holding_seconds <= :managed_horizon",
                    "net_return_pct IS NOT NULL"):
        assert dropped not in sql
    assert [r['shadow_id'] for r in records] == ['a']
    assert records[0]['net_return_pct'] == -1.5 and records[0]['label_resolved_at'] == T+timedelta(hours=2)
    with pytest.raises(ValueError):
        await service._load_shadow_data(db, 'user', 30, ['L3'], dataset_valid_from=T,
            dataset_query_cutoff=T+timedelta(days=2), maturity_embargo_margin_minutes=60,
            managed_contract=spec, managed_label_overrides={'a': dict(overrides['a'], contract_hash='0'*64)})


def test_v2_keeps_the_loser_that_v1_drops_for_degraded_flow(policy):
    # The production bias: a long-lived loser whose flow degraded post-warmup.
    shadow, rows, _ = scenario(policy, 'SL_LATE', invalid=(2, 3), timeout_candles=10)
    assert certify(shadow, rows, None, checked_at=T+timedelta(days=2))['reason'] == 'INCOMPLETE_FLOW_EVIDENCE'
    proof = certify(shadow, rows, None, checked_at=T+timedelta(days=2), certification=V2)
    assert proof['valid'] and proof['label'] == 0 and proof['degraded_flow_decisions'] == 2


# --- activation of l3_managed_cert_v2 through config ---
from app.ml.l3_managed_exit import CERTIFICATION_KEY, apply_proof, configured_certification


def test_configured_certification_is_optional_and_strict(policy):
    _, _, cfg = scenario(policy)
    assert configured_certification(cfg) is None and configured_certification(None) is None
    assert configured_certification({**cfg, CERTIFICATION_KEY: V2}) == V2
    with pytest.raises(ValueError):
        configured_certification({**cfg, CERTIFICATION_KEY: {**V2, 'flow_evidence': 'ANY'}})


def test_apply_proof_clears_label_fields_when_a_recertified_proof_is_invalid():
    shadow = SimpleNamespace(eligible_for_training=True, net_return_pct=0.4, label_resolved_at=T)
    apply_proof(shadow, {'valid': False, 'reason': 'PRICE_HISTORY_GAP'})
    assert (shadow.eligible_for_training, shadow.net_return_pct, shadow.label_resolved_at) == (False, None, None)
    apply_proof(shadow, {'valid': True, 'net_return_pct': -1.2, 'label_available_at': T.isoformat()})
    assert (shadow.eligible_for_training, shadow.net_return_pct, shadow.label_resolved_at) == (True, -1.2, T)


@pytest.mark.asyncio
async def test_persisted_loader_segregates_by_certification_version_and_admits_policy_timeouts(policy):
    from app.services.ml_challenger_service import MLChallengerService
    from unittest.mock import AsyncMock, MagicMock
    _, _, cfg = scenario(policy)
    result = MagicMock(); result.fetchall.return_value = []
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    kwargs = dict(dataset_valid_from=T, dataset_query_cutoff=T+timedelta(days=2), maturity_embargo_margin_minutes=60,
                  managed_contract=definition(cfg))
    await MLChallengerService()._load_shadow_data(db, 'user', 30, ['L3'], **kwargs)
    v1_sql = str(db.execute.call_args.args[0])
    assert "certification_version" not in v1_sql and "holding_seconds <= :managed_horizon\n" in v1_sql + "\n"
    await MLChallengerService()._load_shadow_data(db, 'user', 30, ['L3'], managed_certification=V2, **kwargs)
    sql, params = str(db.execute.call_args.args[0]), db.execute.call_args.args[1]
    assert "sx.state->'ml_label'->>'certification_version'=:certification_version" in sql
    assert params['certification_version'] == CERTIFICATION_V2
    assert "(holding_seconds <= :managed_horizon OR outcome = 'TIMEOUT')" in sql
    strict = dict(V2, timeout_horizon='EXIT_AT')
    await MLChallengerService()._load_shadow_data(db, 'user', 30, ['L3'], managed_certification=strict, **kwargs)
    assert "OR outcome = 'TIMEOUT'" not in str(db.execute.call_args.args[0])


def test_label_contract_identity_changes_only_when_rules_are_configured(policy):
    from app.ml.l3_integrity import contract_definitions
    from app.ml.feature_extractor import FEATURE_COLUMNS
    _, _, cfg = scenario(policy)
    base = {**cfg, 'ml_label_version': VERSION, 'ml_label_objective': 'positive_net_return',
            'ml_active_barrier_contract_version': 'shadow_atr_dynamic_v3', 'ml_fee_roundtrip_pct': .2,
            'ml_win_fast_threshold_seconds': 14400}
    plain = contract_definitions(base, FEATURE_COLUMNS)
    assert 'certification' not in plain['label']
    v2 = contract_definitions({**base, CERTIFICATION_KEY: V2}, FEATURE_COLUMNS)
    assert v2['label']['certification'] == V2 and v2['label_id'] != plain['label_id']
    assert contract_definitions(base, FEATURE_COLUMNS)['label_id'] == plain['label_id']  # stable without rules


@pytest.mark.asyncio
async def test_config_service_rejects_bad_certification_and_frontier_before_any_write(policy):
    from app.services.config_service import ConfigService
    _, _, cfg = scenario(policy)
    service = ConfigService(); service.redis = None
    for bad in ({**cfg, CERTIFICATION_KEY: {**V2, 'version': 'x'}}, {CERTIFICATION_KEY: V2},
                {**cfg, 'ml_l3_dataset_valid_from': 'not-a-date'}):
        with pytest.raises(ValueError):
            await service.update_config(None, 'ml', 'user', bad, 'user')
    with pytest.raises(ValueError):
        await service.update_config(None, 'shadow_l3_exit_runtime', 'user', {'version': 'shadow_l3_exit_runtime_v1'}, 'user')
