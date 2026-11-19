"""Behavioral evidence for the explanatory contract, not a new trading policy."""
from copy import deepcopy
import json
import random

import pytest

from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.services.mtf_chart_engine import evaluate, patterns
from app.services.mtf_trigger_diagnostics import pattern_conditions, finalize_diagnostics, passed
from test_mtf_chart_engine import candle, setup_frames


@pytest.fixture
def cfg():
    return MTFChartConfig.model_validate(experimental_preset())


def rows(result):
    return {r['id']: r for r in result['trigger_diagnostics']['checks']}


def test_armed_explains_missing_trigger_without_fabricating_plan(cfg):
    frames = setup_frames()
    frames['5m']['patterns'] = []
    result = evaluate('TEST_USDT', frames, cfg, 28800, .01, 'UP')
    r = rows(result)
    assert result['status'] == 'ARMED'
    assert r['trigger']['state'] == 'FAIL'
    assert r['zone']['state'] == 'PASS'
    assert r['score']['actual'] == result['score']['total']
    for key in ('stop_valid', 'stop_distance', 'targets', 'risk_valid', 'risk_reward', 'plan'):
        assert r[key]['state'] == 'WAITING'
    assert r['risk_reward']['actual'] is None
    assert r['score:volume']['role'] == 'score'
    assert r['score:volume']['actual'] == 3
    json.dumps(result, allow_nan=False)


def test_trigger_diagnostics_do_not_promote_score_components_to_vetoes(cfg):
    cfg.min_score = 0
    cfg.vol_mult = 100
    result = evaluate('TEST_USDT', setup_frames(), cfg, 28800, .01, 'UP')
    assert result['status'] == 'TRIGGERED'
    r = rows(result)
    assert r['score:volume']['state'] == 'FAIL' and r['score:volume']['role'] == 'score'
    assert r['score:volume']['gap'] == 97
    assert r['risk_reward']['actual'] == result['plan']['rr_net']
    assert all(x['state'] in ('PASS', 'DISABLED') for x in r.values() if x['role'] != 'score')


def test_disabled_veto_and_missing_data_remain_distinct(cfg):
    cfg.veto_filters.btc_downtrend = False
    cfg.veto_filters.spread = False
    result = evaluate('TEST_USDT', setup_frames(), cfg, 28800, None, None)
    r = rows(result)
    assert r['btc_downtrend']['state'] == r['spread']['state'] == 'DISABLED'
    assert r['spread_available']['state'] == 'FAIL'
    assert result['vetoes'] == ['SPREAD_UNAVAILABLE']
    missing = evaluate('TEST_USDT', {}, cfg, 28800, None, None)
    assert rows(missing)['trigger']['state'] == 'WAITING'
    assert rows(missing)['score']['actual'] is None
    assert missing['trigger_diagnostics']['patterns'] == []


def test_service_freshness_updates_diagnostic_and_score(cfg):
    result = evaluate('TEST_USDT', setup_frames(), cfg, 28800, .01, 'UP')
    result.update(observed_at_utc='observed', config_hash='frozen', data_errors={})
    result['vetoes'].append('DATA_STALE:5m')
    result['status'] = 'NO_TRADE'
    result['score']['total'] = 0
    finalize_diagnostics(result, cfg, 30000, 28800)
    assert rows(result)['evaluation_current']['state'] == 'FAIL'
    assert rows(result)['score']['actual'] == 0
    assert result['trigger_diagnostics']['status'] == 'NO_TRADE'
    assert result['trigger_diagnostics']['config_hash'] == 'frozen'


def test_pattern_boundaries_alternatives_and_missing_previous(cfg):
    c = candle(o=100, c=101, l=96, h=101)
    rules = pattern_conditions(c, None, 1, cfg)
    assert passed(rules['PIN_BULL'])
    assert not passed(rules['ENGULF_BULL'])
    assert any(r['state'] == 'UNAVAILABLE' for r in rules['ENGULF_BULL'])
    cfg.pin_wick_ratio = 4
    wick = next(x for x in pattern_conditions(c, None, 1, cfg)['PIN_BULL'] if x['id']=='wick_body')
    assert wick['actual'] == wick['expected'] == 4 and wick['state']=='PASS'
    cfg.pin_wick_ratio = 4.001
    assert not passed(pattern_conditions(c, None, 1, cfg)['PIN_BULL'])
    zero_body = candle(o=101, c=101, l=96, h=101)
    assert 'PIN_BULL' in {x['pattern'] for x in patterns(zero_body, None, 1, cfg)}
    for atr in (None, 0):
        assert not patterns(c, None, atr, cfg)


def legacy_patterns(c, previous, atr, cfg):
    """Frozen pre-diagnostic equations: independent regression oracle."""
    amplitude = c['high']-c['low']
    if not atr or amplitude <= 0 or amplitude < cfg.min_range_atr*atr:
        return set()
    body = abs(c['close']-c['open']); upper = c['high']-max(c['open'],c['close']); lower = min(c['open'],c['close'])-c['low']
    bull = c['close']>c['open']; found=set()
    if body <= amplitude*cfg.doji_body_pct: found.add('DOJI')
    if lower>=cfg.pin_wick_ratio*body and lower>=cfg.pin_wick_pct*amplitude and c['close']>=c['high']-amplitude*cfg.pin_close_fraction: found.add('PIN_BULL')
    if upper>=cfg.pin_wick_ratio*body and upper>=cfg.pin_wick_pct*amplitude and c['close']<=c['low']+amplitude*cfg.pin_close_fraction: found.add('PIN_BEAR')
    if upper+lower<=cfg.marubozu_wick_pct*amplitude: found.add('MARUBOZU_BULL' if bull else 'MARUBOZU_BEAR')
    if previous:
        prev_bull = previous['close']>previous['open']
        low,high=(c['low'],c['high']) if cfg.engulf_include_wicks else sorted([c['open'],c['close']])
        plow,phigh=(previous['low'],previous['high']) if cfg.engulf_include_wicks else sorted([previous['open'],previous['close']])
        if bull!=prev_bull and body>0 and previous['close']!=previous['open'] and low<=plow and high>=phigh: found.add('ENGULF_BULL' if bull else 'ENGULF_BEAR')
        if c['low']>previous['low'] and c['high']<previous['high']: found.add('INSIDE_BAR')
        if min(c['open'],c['close'])>min(previous['open'],previous['close']) and max(c['open'],c['close'])<max(previous['open'],previous['close']): found.add('HARAMI')
    return found


@pytest.mark.parametrize('wicks', [False, True])
def test_shared_pattern_predicates_preserve_legacy_semantics(cfg, wicks):
    cfg.engulf_include_wicks=wicks
    rng=random.Random(472)
    for scale in (1, .000001, 10000):
        for i in range(200):
            cs=[]
            for j in range(2):
                o=rng.randint(90,110)*scale; close=rng.randint(90,110)*scale
                cs.append(candle(o=o,c=close,h=max(o,close)+rng.randint(0,10)*scale,l=min(o,close)-rng.randint(0,10)*scale))
            c,previous=cs
            if i%7==0: previous=None
            atr=rng.choice([None,0,.1*scale,10*scale])
            assert {p['pattern'] for p in patterns(c,previous,atr,cfg)} == legacy_patterns(c,previous,atr,cfg)


def test_limits_are_frozen_in_diagnostic_and_inputs_untouched(cfg):
    frames=setup_frames(); before=deepcopy(frames)
    result=evaluate('TEST_USDT',frames,cfg,28800,.4,'DOWN')
    old=deepcopy(result)
    cfg.max_spread_pct=.9
    cfg.min_score=0
    assert result == old and frames==before
    assert rows(result)['spread']['expected'] == .2
    assert rows(result)['btc_downtrend']['actual'] == 'DOWN'
