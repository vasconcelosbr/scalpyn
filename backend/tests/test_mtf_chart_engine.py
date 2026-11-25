from copy import deepcopy
import math
import pandas as pd
import pytest

from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.services.mtf_chart_engine import closed_candles, patterns, pivots, indicator_history, evaluate, simulate_step, prepare_frame
from app.services.feature_engine import FeatureEngine


@pytest.fixture
def cfg():
    return MTFChartConfig.model_validate(experimental_preset())


def candle(t=0, o=100, h=103, l=98, c=102, v=100):
    return dict(time=t, open=o, high=h, low=l, close=c, volume=v)


def test_required_config_and_presets():
    data = experimental_preset()
    del data['stop_buffer_atr']
    with pytest.raises(ValueError):
        MTFChartConfig.model_validate(data)
    data = experimental_preset()
    data['calibrated'] = True
    with pytest.raises(ValueError):
        MTFChartConfig.model_validate(data)


def test_closed_boundary_and_gap():
    cs = [candle(t=0), candle(t=300)]
    assert len(closed_candles(cs, '5m', 300)) == 1
    assert len(closed_candles(cs, '5m', 299)) == 0
    with pytest.raises(ValueError, match='GAP'):
        closed_candles([candle(t=0), candle(t=600)], '5m', 900)
    with pytest.raises(ValueError, match='INVALID'):
        closed_candles([candle(v=float('nan'))], '5m', 300)


@pytest.mark.parametrize('c,previous,expected', [
    (candle(o=100,c=100.1), None, 'DOJI'),
    (candle(o=101,c=102,h=102,l=96), None, 'PIN_BULL'),
    (candle(o=100,c=99,h=105,l=99), None, 'PIN_BEAR'),
    (candle(o=99,c=103,h=103,l=99), candle(o=102,c=100), 'ENGULF_BULL'),
    (candle(o=103,c=99,h=103,l=99), candle(o=100,c=102), 'ENGULF_BEAR'),
    (candle(o=100,c=103,h=103,l=100), None, 'MARUBOZU_BULL'),
    (candle(o=103,c=100,h=103,l=100), None, 'MARUBOZU_BEAR'),
    (candle(o=100.5,c=101,h=102,l=99), candle(o=100,c=102,h=103,l=98), 'HARAMI'),
    (candle(o=100,c=101,h=102,l=99), candle(h=103,l=98), 'INSIDE_BAR'),
])
def test_patterns(c, previous, expected, cfg):
    assert expected in {p['pattern'] for p in patterns(c, previous, 1, cfg)}
    assert patterns(c, previous, 1000, cfg) == []
    assert patterns(candle(o=100,h=100,l=100,c=100), previous, 1, cfg) == []


def test_pivot_confirmation_no_future(cfg):
    cs = [candle(t=i*300,h=103+i if i <= 3 else 109-i) for i in range(7)]
    assert pivots(cs[:6], '5m', cfg) == []
    points = pivots(cs, '5m', cfg)
    assert points[0]['time'] == 900
    assert points[0]['confirmed_at'] == 2100


def test_indicator_parity_and_prefix_invariance(cfg):
    cs = [candle(t=i*300,o=100+math.sin(i),c=100+math.cos(i),h=103,l=97,v=i+1) for i in range(65)]
    config = {'rsi': {'enabled': True, 'period': 14}, 'ema': {'enabled': True, 'periods': [9,21]},
              'adx': {'enabled': True, 'period':14}, 'atr': {'enabled': True, 'period':14}}
    history = indicator_history(cs, config, set(config))
    engine = FeatureEngine(config)
    df = pd.DataFrame(cs)
    for family in config:
        values = getattr(engine, f'_calc_{family}')(df)
        for key,value in values.items():
            if value is not None:
                assert history[key][-1]['value'] == value
    before = prepare_frame(cs[:50], '5m', 50*300, config, set(config), cfg)
    after = prepare_frame(cs, '5m', 50*300, config, set(config), cfg)
    assert before == after


def pending():
    return {'status':'PENDING','observed_at':310,'expires_at':1500,'last_candle':-1,
            'entry':100,'stop':99,'tp1':110,'tp2':120,'pnl_usdt':None,'pnl_pct':None}


def test_no_retroactive_fill_and_stop_first(cfg):
    state = pending()
    assert simulate_step(state, candle(t=300), cfg) is None
    result = simulate_step(state, candle(t=600,o=100,h=121,l=98,c=100), cfg)
    assert result['status'] == 'SL'
    assert result['ambiguous']
    assert result['fill'] > state['entry']
    assert result['pnl_usdt'] < 0
    assert result['pnl_pct'] == pytest.approx(result['pnl_usdt']/result['invested']*100)
    assert state == pending()
    assert simulate_step(result, candle(t=900), cfg) is None


def test_partial_breakeven_and_capital(cfg):
    s = simulate_step(pending(), candle(t=600,o=100,h=111,l=100,c=110), cfg)
    assert s['status'] == 'OPEN' and s['partial']
    assert s['remaining'] == pytest.approx(s['quantity']/2)
    assert s['stop'] > s['fill']
    assert s['invested'] <= cfg.capital_usdt
    s2 = simulate_step(s, candle(t=900,o=110,h=121,l=105,c=120), cfg)
    assert s2['status'] == 'TP' and s2['pnl_usdt'] > 0


def test_entry_expiry_and_gap(cfg):
    assert simulate_step(pending(), candle(t=1500), cfg)['reason'] == 'ENTRY_EXPIRED'
    assert simulate_step(pending(), candle(t=600,o=98), cfg)['reason'] == 'GAP_INVALIDATED'


def test_missing_data_no_authority(cfg):
    result = evaluate('XRP_USDT', {}, cfg, 300, None, None)
    assert result['status'] == 'NO_TRADE' and result['score']['total'] == 0
    assert result['plan'] is None and result['operational_effect'] is False


def setup_frames():
    directions = [{'time':i,'price':p,'kind':k,'label':l,'confirmed_at':i+1} for i,p,k,l in
        [(0,90,'L','L'),(1,100,'H','H'),(2,95,'L','HL'),(3,110,'H','HH')]]
    zone_points = [{'time':i,'price':p,'kind':k,'label':k,'confirmed_at':i+1} for i,(p,k) in
        enumerate([(98.5,'L'),(99,'L'),(110,'H'),(110.5,'H'),(120,'H'),(120.5,'H')])]
    last = candle(t=28500,o=99,h=100.1,l=99,c=100,v=300)
    base = [candle(t=28500-(20-i)*300,v=100) for i in range(20)]+[last]
    return {'4h': {'candles':[candle(t=14400)],'atr':2,'pivots':directions,'patterns':[]},
            '1h': {'candles':[candle(t=25200)],'atr':2,'pivots':zone_points,'patterns':[]},
            '5m': {'candles':base,'atr':1,'pivots':[],'patterns':[{'time':28500,'pattern':'ENGULF_BULL','metrics':{}}]}}


def test_trigger_score_veto_rr_and_neutral_states(cfg):
    frames = setup_frames()
    result = evaluate('BTC_USDT', frames, cfg, 28800, 0.01, 'UP')
    assert result['status'] == 'TRIGGERED'
    assert result['plan']['rr_net'] < result['plan']['rr_gross']
    assert result['score']['total'] == 8
    assert result['plan']['notional_usdt'] <= cfg.capital_usdt
    blocked = evaluate('ALT_USDT', frames, cfg, 28800, 0.01, 'DOWN')
    assert blocked['status'] == 'NO_TRADE' and blocked['score']['total'] == 0
    assert 'BTC_DOWNTREND' in blocked['vetoes']
    missing_spread = evaluate('BTC_USDT', frames, cfg, 28800, None, 'UP')
    assert 'SPREAD_UNAVAILABLE' in missing_spread['vetoes']
    frames['5m']['patterns'] = []
    assert evaluate('BTC_USDT', frames, cfg, 28800, 0.01, 'UP')['status'] == 'ARMED'
    frames['1h']['pivots'] = []
    assert evaluate('BTC_USDT', frames, cfg, 28800, 0.01, 'UP')['status'] == 'WAIT'


def test_stop_sized_from_gross_rr_without_enabling_rr_veto(cfg):
    frames = setup_frames()
    frames['5m']['candles'][-1].update(open=100, low=99.95)
    data = cfg.model_dump()
    data['min_score'] = 0
    data['risk_per_trade_pct'] = .1
    data['veto_filters']['risk_reward'] = False
    legacy = MTFChartConfig.model_validate(data)
    before = evaluate('BTC_USDT', frames, legacy, 28800, .01, 'UP')
    data['stop_sizing'] = {'enabled': True, 'target_pct': 1, 'gross_rr': 2}
    sized = MTFChartConfig.model_validate(data)
    after = evaluate('BTC_USDT', frames, sized, 28800, .01, 'UP')
    plan = after['plan']
    assert after['status'] == 'TRIGGERED'
    assert plan['stop'] == pytest.approx(99.5)
    assert plan['stop'] < before['plan']['stop']
    assert plan['stop_sizing']['stop_pct'] == .5
    assert (plan['stop_sizing']['reference_target']-plan['entry'])/(plan['entry']-plan['stop']) == pytest.approx(2)
    assert [plan['tp1'], plan['tp2']] == [before['plan']['tp1'], before['plan']['tp2']]
    assert plan['quantity'] < before['plan']['quantity']
    assert not sized.veto_filters.risk_reward
    assert sized.engine_dump() != legacy.engine_dump()
    # A later config edit must never resize an already frozen Shadow stop.
    changed = sized.model_copy(update={'stop_sizing': sized.stop_sizing.model_copy(update={'gross_rr': 4})})
    pending_state = {**plan,'status':'PENDING','observed_at':28801,'expires_at':30000,'last_candle':-1}
    stepped = simulate_step(pending_state, candle(t=29100,o=100,h=100.2,l=99.6,c=100),changed)
    assert stepped['status']=='OPEN' and stepped['stop']==plan['stop']


def test_stop_sizing_validation_and_disabled_hash_compatibility(cfg):
    data=cfg.model_dump()
    data.pop('stop_sizing')
    legacy=MTFChartConfig.model_validate(data)
    assert 'stop_sizing' not in legacy.engine_dump()
    data['stop_sizing']={'enabled':False,'target_pct':2,'gross_rr':3}
    assert MTFChartConfig.model_validate(data).engine_dump()==legacy.engine_dump()
    for target,rr in [(1,0),(1,-1),(1,float('nan')),(1,float('inf')),(100,1)]:
        data['stop_sizing']={'enabled':True,'target_pct':target,'gross_rr':rr}
        with pytest.raises(ValueError): MTFChartConfig.model_validate(data)


def test_frozen_window_matches_producer(cfg):
    config = {'ema': {'enabled':True,'periods':[9]}, 'atr':{'enabled':True,'period':14}}
    cs = [candle(t=i*300,c=100+math.sin(i)) for i in range(80)]
    history = indicator_history(cs, config, {'ema','atr'}, window=50)
    expected = FeatureEngine(config)._calc_ema(pd.DataFrame(cs[-50:]))['ema9']
    assert history['ema9'][-1]['value'] == expected


def test_missing_future_path_is_unresolved(cfg):
    s = simulate_step(pending(), candle(t=600,o=100,h=101,l=99.5,c=100), cfg)
    assert s['status'] == 'OPEN'
    gap = simulate_step(s, candle(t=1200,o=100,h=121,l=99.5,c=120), cfg)
    assert gap['status'] == 'UNRESOLVED' and gap['pnl_pct'] is None


def test_pending_missing_first_fill_is_not_filled_late(cfg):
    s = simulate_step(pending(), candle(t=900,o=100,h=101,l=99.5,c=100), cfg)
    assert s['status'] == 'UNRESOLVED' and s['reason'] == 'CANDLE_GAP'


def test_hourly_close_invalidates_frozen_zone(cfg):
    s = simulate_step({**pending(), 'invalidation_zone_low':101}, candle(t=600,o=100,h=101,l=99.5,c=100), cfg, hourly_close=100)
    assert s['status'] == 'EXPIRED' and s['reason'] == 'ZONE_INVALIDATED'
    assert s['pnl_usdt'] < 0
