from copy import deepcopy

import pytest

from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.schemas.mtf_rules import Condition, RuleGroup, DURATIONS
from app.services.mtf_config_contract import complete_config
from app.services.mtf_chart_v3 import calculate_frames
from app.services.mtf_chart_engine import evaluate, prepare_frame
from app.services.mtf_forming_preview import forming_preview
from app.services.mtf_rules_engine import evaluate_group


def fixture(version):
    config = experimental_preset()
    indicators = {'atr': {'enabled': True, 'period': 14}, 'ema': {'enabled': True, 'periods': [9, 21]}}
    if version == 'v3':
        config = complete_config(config, indicators)
        for instance in config['indicators'].values():
            instance['sample_window'] = max(30, instance['parameters'].get('period', 0))
    cfg = MTFChartConfig.model_validate(config)
    now = 60 * DURATIONS['4h'] + 100
    raw = {}
    for tf in cfg.active_timeframes():
        end = now - now % DURATIONS[tf]
        raw[tf] = [dict(time=end-(49-i)*DURATIONS[tf], open=100+i/10, high=102+i/10,
                        low=98+i/10, close=99.9+i/10, volume=100+i, is_closed=i != 49)
                   for i in range(50)]
    cutoff = now - now % DURATIONS[cfg.stage('trigger')]
    if version == 'v3':
        frames, series, errors = calculate_frames(raw, cfg, cutoff)
        assert not errors
    else:
        frames = {tf: prepare_frame(candles, tf, cutoff, indicators, {'atr', 'ema'}, cfg, 30)
                  for tf, candles in raw.items()}
        series = None
    return cfg, indicators, now, cutoff, raw, frames, series


@pytest.mark.parametrize('version', ['v1', 'v3'])
def test_forming_values_change_without_changing_official_decision_or_inputs(version):
    cfg, indicators, now, cutoff, raw, frames, series = fixture(version)
    original_frames, original_raw, original_config = deepcopy(frames), deepcopy(raw), cfg.model_dump()
    official = evaluate('BTC_USDT', frames, cfg, cutoff, .01, 'UP', rule_series=series)
    args = dict(indicators=indicators, families={'atr', 'ema'}, windows={tf:30 for tf in raw})
    first = forming_preview('BTC_USDT', raw, frames, cfg, now, .01, 'UP', **args)
    assert first['state'] == 'AVAILABLE'
    assert raw == original_raw and frames == original_frames and cfg.model_dump() == original_config
    changed = deepcopy(raw)
    changed[cfg.stage('trigger')][-1].update(close=110, high=111)
    second = forming_preview('BTC_USDT', changed, frames, cfg, now, .01, 'UP', **args)
    assert first['diagnostics']['patterns'] != second['diagnostics']['patterns']
    assert first['confirmation'] == 'CLOSE_ONLY' and first['operational_effect'] is False
    assert not {'status', 'plan', 'trigger_5m', 'analysis'} & second.keys()
    assert evaluate('BTC_USDT', frames, cfg, cutoff, .01, 'UP', rule_series=series) == official
    if version == 'v3':
        closed_again, series_again, _ = calculate_frames(changed, cfg, cutoff)
        assert evaluate('BTC_USDT', closed_again, cfg, cutoff, .01, 'UP', rule_series=series_again) == official


def test_missing_stale_or_invalid_forming_candle_never_falls_back_to_closed_as_live():
    cfg, _, now, _, raw, frames, _ = fixture('v3')
    for mutation in ('missing', 'already_closed', 'invalid', 'future'):
        changed = deepcopy(raw)
        candle = changed[cfg.stage('trigger')][-1]
        if mutation == 'missing': changed[cfg.stage('trigger')].pop()
        elif mutation == 'already_closed': candle['is_closed'] = True
        elif mutation == 'invalid': candle['close'] = float('nan')
        else: candle['time'] += DURATIONS[cfg.stage('trigger')]
        result = forming_preview('BTC_USDT', changed, frames, cfg, now, .01, 'UP')
        assert result['state'] == 'UNAVAILABLE' and result['diagnostics'] is None


def test_intrabar_cross_uses_natural_timestamps_and_never_confirms_default_rules():
    rule = RuleGroup(logic='AND', conditions=[Condition(id='cross', label='Cross',
        left={'ref':'indicators.fast'}, operator='crosses_above', right={'value':10})])
    series = {'indicators.fast': [dict(time=t, close_at=t+60, timeframe='1m', value=v, valid_until=t+120)
                                  for t,v in [(0,8),(60,9),(120,11),(180,999)]]}
    before = deepcopy(series)
    assert evaluate_group(rule, {}, series, 150)[0] == 'FAIL'
    state, checks = evaluate_group(rule, {}, series, 150, preview_at=150)
    assert state == 'PASS' and checks[0]['actual'] == 11
    assert checks[0]['previous']['actual'] == 9
    assert series == before
    assert evaluate_group(rule, {}, series, 150)[0] == 'FAIL'


def test_preview_v3_recalculates_open_higher_timeframe_indicators():
    cfg, _, now, cutoff, raw, _, _ = fixture('v3')
    _, closed_series, _ = calculate_frames(raw, cfg, cutoff)
    changed = deepcopy(raw)
    changed['4h'][-1].update(close=110, high=111)
    _, preview_series, errors = calculate_frames(changed, cfg, now, forming_at=now)
    assert not errors
    key = next(k for k,v in preview_series.items() if v and v[-1]['timeframe'] == '4h')
    assert preview_series[key][-1]['time'] == changed['4h'][-1]['time']
    assert preview_series[key][-1]['close_at'] > now
    assert closed_series[key][-1]['time'] < preview_series[key][-1]['time']
