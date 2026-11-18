"""Each observational strategy veto can be disabled without bypassing data integrity."""
import pytest
from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.services.mtf_chart_engine import evaluate, simulate_step
from app.services.profile_runtime_config import canonical_hash
from test_mtf_chart_engine import setup_frames, pending, candle


def config(**updates):
    return MTFChartConfig.model_validate({**experimental_preset(), **updates})


def run(cfg, frames=None, spread=.01, btc='UP'):
    return evaluate('RAIN_USDT', frames or setup_frames(), cfg, 28800, spread, btc)


def test_veto_defaults_preserve_existing_settings_and_engine_identity():
    raw = experimental_preset()
    raw.pop('veto_filters')
    raw.update(min_score=0, slippage_pct=.1, atr_period=14)
    expected = config(**raw).engine_dump()
    assert 'veto_filters' not in expected
    loaded = MTFChartConfig.model_validate(raw)
    assert loaded.min_score == 0 and loaded.slippage_pct == .1 and loaded.atr_period == 14
    assert all(loaded.veto_filters.model_dump().values())
    assert MTFChartConfig.model_validate(loaded.model_dump()).engine_dump() == expected
    changed = loaded.model_copy(deep=True)
    changed.veto_filters.btc_downtrend = False
    assert canonical_hash(changed.engine_dump()) != canonical_hash(expected)
    assert raw.get('veto_filters') is None


@pytest.mark.parametrize('key,code,updates,spread,btc', [
    ('btc_downtrend','BTC_DOWNTREND',{},.01,'DOWN'),
    ('spread','SPREAD_TOO_WIDE',{'max_spread_pct':.005},.01,'UP'),
    ('stop_distance','STOP_TOO_WIDE',{'max_stop_atr':.5},.01,'UP'),
    ('risk_reward','RR_BELOW_MIN',{'min_rr_net':20,'rr_bonus_threshold':20},.01,'UP'),
])
def test_strategy_veto_switch_removes_only_its_rule(key, code, updates, spread, btc):
    cfg = config(min_score=0, **updates)
    assert code in run(cfg,spread=spread,btc=btc)['vetoes']
    setattr(cfg.veto_filters,key,False)
    result = run(cfg,spread=spread,btc=btc)
    assert code not in result['vetoes'] and result['status']=='TRIGGERED'
    assert result['operational_effect'] is False
    assert 'SPREAD_UNAVAILABLE' in run(cfg,spread=None,btc=btc)['vetoes']


@pytest.mark.parametrize('key,code,labels',[
    ('asset_downtrend','SPOT_NOT_BUYING',['L','H','LL','LH']),
    ('asset_range','TREND_RANGE',['L','H','HL','LH']),
])
def test_direction_switch_keeps_direction_and_score(key,code,labels):
    frames=setup_frames()
    for p,label in zip(frames['4h']['pivots'],labels):p['label']=label
    frames['4h']['pivots'][-1]['price']=99
    if key=='asset_downtrend':frames['4h']['pivots'][-2]['price']=89
    cfg=config(min_score=0)
    assert code in run(cfg,frames)['vetoes']
    setattr(cfg.veto_filters,key,False)
    result=run(cfg,frames)
    assert result['status']=='TRIGGERED' and code not in result['vetoes']
    assert result['context_4h']['trend']!='UP' and result['score']['components']['trend']==0


def test_btc_disabled_no_longer_requires_btc_but_keeps_other_vetoes():
    cfg=config()
    assert 'BTC_CONTEXT_UNAVAILABLE' in run(cfg,btc=None)['vetoes']
    cfg.veto_filters.btc_downtrend=False
    assert 'BTC_CONTEXT_UNAVAILABLE' not in run(cfg,btc=None)['vetoes']
    cfg.max_spread_pct=.001
    assert 'SPREAD_TOO_WIDE' in run(cfg,btc='DOWN')['vetoes']


def test_disabled_strategy_filters_do_not_disable_data_or_plan_validation():
    cfg=config(veto_filters={k:False for k in config().veto_filters.model_dump()})
    assert 'SPREAD_UNAVAILABLE' in run(cfg,spread=None)['vetoes']
    frames=setup_frames(); frames['1h']['atr']=None
    assert 'DATA_MISSING:1h' in run(cfg,frames)['vetoes']
    frames=setup_frames(); frames['4h']['candles'][0]['time']=-100000
    assert 'DATA_STALE:4h' in run(cfg,frames)['vetoes']
    frames=setup_frames(); frames['1h']['pivots']=[p for p in frames['1h']['pivots'] if p['kind']=='L']
    assert 'STRUCTURAL_TARGETS_UNAVAILABLE' in run(cfg,frames)['vetoes']
    cfg.stop_buffer_atr=200
    assert 'INVALID_PLAN' in run(cfg)['vetoes']


def test_rr_toggle_is_frozen_into_simulation_and_applies_at_fill():
    cfg=config(min_rr_net=20,rr_bonus_threshold=20)
    bar=candle(t=600,o=100,h=101,l=99.5,c=100)
    assert simulate_step(pending(),bar,cfg)['reason']=='FILL_RR_BELOW_MIN'
    snapshot=cfg.model_dump()
    cfg.veto_filters.risk_reward=False
    assert simulate_step(pending(),bar,cfg)['status']=='OPEN'
    assert simulate_step(pending(),bar,MTFChartConfig.model_validate(snapshot))['reason']=='FILL_RR_BELOW_MIN'


@pytest.mark.parametrize('value',['false',0,None])
def test_veto_values_require_explicit_booleans(value):
    with pytest.raises(ValueError):config(veto_filters={'btc_downtrend':value})
