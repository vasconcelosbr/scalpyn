from copy import deepcopy
import pytest
from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.services.mtf_risk_audit import audit, finalize, structure, publication
from app.services.profile_runtime_config import canonical_hash


def fixture():
    cfg = MTFChartConfig.model_validate(experimental_preset())
    cfg.context_audit.enabled = True
    cfg.stop_sizing.enabled = True
    points = [dict(kind=k, label=l, price=p, time=t, confirmed_at=t+1)
              for t,k,l,p in [(0,'L','L',97),(1,'H','H',99),(2,'L','HL',99.6),(3,'H','HH',101)]]
    candle = dict(time=0, open=100, high=101, low=99.8, close=100, volume=10)
    frames = {tf:dict(atr=.2,pivots=deepcopy(points),candles=[deepcopy(candle)],patterns=[])
              for tf in ['4h','1h','5m']}
    result = dict(status='TRIGGERED', symbol='BTC_USDT', score={'total':10}, vetoes=[],
                  plan={'entry':100,'stop':99.5,'expires_at':'1970-01-01T04:10:00+00:00'},
                  zone_1h={'low':99.7}, trigger_diagnostics={'checks':[]},
                  candle_close_at_utc='1970-01-01T04:00:00+00:00',
                  config_hash=canonical_hash(cfg.engine_dump()))
    return cfg, frames, result


def test_pass_and_read_time_contract():
    cfg, frames, r = fixture()
    finalize(r, frames, cfg, 14400)
    assert r['risk_audit']['status'] == 'PASS'
    publication(r,cfg,14401)
    assert r['signal_contract']['eligible']
    sid=r['signal_contract']['signal_id']
    publication(r,cfg,15000)
    assert not r['signal_contract']['eligible'] and r['signal_contract']['signal_id']==sid
    r['config_hash']='old'
    publication(r,cfg,14401)
    assert not r['signal_contract']['eligible']


def test_btc_not_exempt_and_score_cannot_compensate():
    cfg, frames, r=fixture()
    frames['1h']['pivots'][-1].update(price=98,label='LH')
    finalize(r,frames,cfg,14400)
    assert r['status']=='NO_TRADE' and r['score']['total']==0
    assert 'audit:zone_alignment' in r['risk_audit']['reasons']


def test_integrity_stays_broken_after_price_recovers():
    cfg, frames, r=fixture()
    f=frames['1h']
    f['candles']=[dict(time=0,open=100,high=101,low=99,close=99,volume=1),
                  dict(time=3600,open=99,high=101,low=99,close=100,volume=1)]
    assert structure(f,'1h',7200)['intact'] is False
    # Equal close tests the level; it does not break it.
    f['candles'][0]['close']=99.6
    assert structure(f,'1h',7200)['intact'] is True


def test_lh_requires_close_above_level_but_not_rejection_after_break():
    cfg, frames, r=fixture()
    frames['5m']['pivots'][-1].update(label='LH',price=100)
    finalize(r,frames,cfg,14400)
    assert r['status']=='ARMED'
    assert 'audit:lh_break' in r['risk_audit']['reasons']
    frames['5m']['candles'][0]['close']=100.1
    assert audit(r,frames,cfg,14400)['status']=='PASS'


@pytest.mark.parametrize('stop,reason',[(99.8,'audit:stop_swing'),(99.7,'audit:stop_zone'),(99.75,'audit:stop_atr'),(99,'audit:reference_rr')])
def test_stop_and_rr_are_independent(stop,reason):
    cfg, frames, r=fixture()
    r['plan']['stop']=stop
    assert reason in audit(r,frames,cfg,14400)['reasons']


def test_costs_missing_data_and_preview_fail_closed():
    cfg, frames, r=fixture()
    cfg.fee_per_side_pct=1
    assert 'audit:reference_reward' in audit(r,frames,cfg,14400)['reasons']
    cfg.fee_per_side_pct=0
    frames['5m']['atr']=None
    assert 'audit:stop_atr' in audit(r,frames,cfg,14400)['reasons']
    cfg, frames, r=fixture()
    finalize(r,frames,cfg,14400,preview=True)
    publication(r,cfg,14401)
    assert not r['signal_contract']['eligible']
    assert r['status']!='TRIGGERED'


def test_causal_prefix_and_disabled_identity():
    cfg, frames, r=fixture()
    original=audit(r,frames,cfg,14400)
    for f in frames.values():
        f['pivots'].append(dict(kind='L',label='LL',price=1,time=100,confirmed_at=20000))
        f['candles'].append(dict(time=20000,open=1,high=2,low=1,close=1,volume=1))
    assert audit(r,frames,cfg,14400)==original
    cfg.context_audit.enabled=False
    assert 'context_audit' not in cfg.engine_dump()
    old=deepcopy(r)
    finalize(r,frames,cfg,14400)
    assert r==old


def test_invalid_configuration():
    raw=experimental_preset()
    for value in [0,-1,float('nan'),float('inf')]:
        raw['context_audit']['min_stop_atr_trigger']=value
        with pytest.raises(ValueError): MTFChartConfig.model_validate(raw)


def test_new_confirmed_structure_heals_and_future_confirmation_does_not():
    _, frames, _=fixture()
    f=frames['1h']
    f['candles']=[dict(time=i*3600,open=100,high=102,low=98,close=99 if i==0 else 101,volume=1) for i in range(5)]
    fresh=[dict(time=t,confirmed_at=16000,kind=k,label=l,price=p) for t,k,l,p in
           [(4000,'L','LL',98),(5000,'H','LH',100),(6000,'L','HL',99),(7000,'H','HH',102)]]
    f['pivots']+=fresh
    assert structure(f,'1h',14400)['intact'] is False
    assert structure(f,'1h',18000)['intact'] is True


def test_reference_rr_float_boundary_and_unknown_version():
    cfg,frames,r=fixture()
    entry=58753.8
    r['plan'].update(entry=entry,stop=entry*(1-.01/2))
    evidence=audit(r,frames,cfg,14400)
    assert next(c for c in evidence['checks'] if c['id']=='audit:reference_rr')['state']=='PASS'
    cfg,frames,r=fixture()
    finalize(r,frames,cfg,14400)
    r['risk_audit']['version']='unknown'
    publication(r,cfg,14401)
    assert not r['signal_contract']['eligible']
