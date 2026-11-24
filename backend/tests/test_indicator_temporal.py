from copy import deepcopy
from datetime import datetime, timezone
import pandas as pd
import pytest
from app.services.feature_engine import FeatureEngine
from app.services.seed_service import DEFAULT_INDICATORS
from app.utils.indicator_merge import envelop_results
from app.services.mtf_replay import freeze_inputs, replay
from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.services.mtf_chart_engine import evaluate


def frame():
    times = pd.date_range('2026-11-23', periods=301, freq='5min', tz='UTC')
    return pd.DataFrame(dict(time=times, open=100., high=102., low=99., close=101., volume=10.))


def test_partial_candle_cannot_repaint_technical_values_and_envelopes():
    df = frame(); cutoff = df.time.iloc[-1].to_pydatetime()
    engine = FeatureEngine(DEFAULT_INDICATORS)
    expected = engine.calculate(df.iloc[:-1], group='microstructure', timeframe='5m', as_of=cutoff)
    df.loc[df.index[-1], ['open','high','low','close','volume']] = [500,999,1,900,100000]
    actual = engine.calculate(df, group='microstructure', timeframe='5m', as_of=cutoff,
                              market_data={'spread_pct': .01, 'close_5m': 900})
    for key, value in expected.items():
        assert actual[key] == value, key
    assert actual['close_5m'] == 101
    envelopes = envelop_results(actual)
    assert envelopes['close_5m']['candle_close_time'] == cutoff.isoformat()
    assert 'source_timestamp' not in envelopes['spread_pct']
    for key in ('ema50_distance_pct','vwap_distance_pct','recent_high_5m_distance_pct'):
        e=envelopes[key]
        assert e['value'] == round((e['base_close']/e['reference_value']-1)*100,4)


def test_epoch_seconds_and_explicit_open_flag():
    df=frame();cutoff=df.time.iloc[-1].to_pydatetime()
    df['time']=df.time.astype('int64')//10**9
    df['is_closed']=True;df.loc[df.index[-2:],'is_closed']=False
    result=FeatureEngine(DEFAULT_INDICATORS).calculate(df,group='microstructure',timeframe='5m',as_of=cutoff)
    assert result.key_metadata['close_5m']['candle_open_time']==datetime.fromtimestamp(int(df.time.iloc[-3]),timezone.utc).isoformat()


def test_replay_is_immutable_and_refuses_unfrozen_or_tampered_inputs():
    cfg=MTFChartConfig.model_validate(experimental_preset())
    frames={tf:{'candles':[],'pivots':[],'patterns':[],'atr':None} for tf in cfg.active_timeframes()}
    analysis=evaluate('BTC_USDT',frames,cfg,1800000000,.01,None)
    data={'analysis':analysis,'frames':frames}
    payload={'analysis':analysis,'replay_inputs':freeze_inputs(data,cfg)}
    before=deepcopy(payload)
    assert replay(payload)['status']=='MATCH'
    assert payload==before
    payload['replay_inputs']['cutoff']+=1
    assert replay(payload)['reason']=='INPUT_HASH_MISMATCH'
    assert replay({'analysis':analysis})['reason']=='LEGACY_INPUTS_NOT_FROZEN'


def test_triggered_decision_replays_without_context_values_or_configuration_changes():
    from test_mtf_chart_engine import setup_frames
    cfg=MTFChartConfig.model_validate({**experimental_preset(), 'min_score': 0})
    frames=setup_frames()
    analysis=evaluate('BTC_USDT',frames,cfg,28800,.01,'DOWN')
    assert analysis['status']=='TRIGGERED'
    analysis.update(spread_pct=.01,btc_trend='DOWN')
    payload={'analysis':analysis,'replay_inputs':freeze_inputs({'analysis':analysis,'frames':frames},cfg),
             'indicators':[{'role':'context_only','name':'close_5m','value':900}]}
    assert replay(payload)['status']=='MATCH'
    assert payload['replay_inputs']['config']==cfg.model_dump()
