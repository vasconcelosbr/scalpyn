from copy import deepcopy
import pytest
from test_mtf_forming_preview import fixture
from app.services.mtf_chart_engine import evaluate
from app.services.profile_runtime_config import canonical_hash
from app.services.mtf_live_preview import LivePreview, gate_candle


def setup(version='v3'):
    cfg, indicators, now, cutoff, raw, frames, series = fixture(version)
    official = evaluate('BTC_USDT', frames, cfg, cutoff, .01, 'UP', rule_series=series)
    official.update(config_hash=canonical_hash(cfg.engine_dump()), spread_pct=.01, btc_trend='UP')
    seed = dict(symbol='BTC_USDT', analysis=official, indicator_config=indicators,
                frames={tf: {**f, 'candles': deepcopy(raw[tf])} for tf, f in frames.items()})
    return LivePreview(seed, cfg, families={'atr','ema'}, windows={tf:30 for tf in raw}), now


def event(live, now, price=110):
    candle = live.raw['5m'][-1]
    return dict(time=now, time_ms=now*1000, channel='spot.candlesticks', event='update',
                result=dict(n='5m_BTC_USDT', t=str(candle['time']), o=str(candle['open']),
                            h=str(max(price, candle['high'])), l=str(candle['low']), c=str(price),
                            a='30', v='99999', w=False))


@pytest.mark.parametrize('version',['v1','v3'])
def test_events_recalculate_immediately_and_cannot_mutate_official_signal(version):
    live, now = setup(version)
    official = deepcopy(live.seed['analysis'])
    first = live.update(event(live, now), now)
    second = live.update(event(live, now+.5, 105), now+.5)
    assert first['preview']['diagnostics'] != second['preview']['diagnostics']
    assert second['candle']['volume'] == 30  # base volume, never quote volume
    assert second['preview']['delivery'] == 'stream'
    assert second['preview']['confirmation'] == 'CLOSE_ONLY'
    assert 'analysis' not in second and 'status' not in second['preview']
    assert live.seed['analysis'] == official


def test_out_of_order_duplicates_wrong_symbol_invalid_or_stale_events_are_ignored():
    live, now = setup()
    message = event(live, now)
    assert live.update(message, now)
    assert live.update(message, now) is None
    assert live.update(event(live, now-1), now) is None
    for field,value in [('n','5m_ETH_USDT'),('c','nan'),('a',None),('l','99999'),('t',str(now+600))]:
        bad = event(live, now+1); bad['result'][field]=value
        assert gate_candle(bad, 'BTC_USDT', live.raw, now+1) is None
    assert live.update(event(live, now+1), now+live.cfg.poll_seconds*live.cfg.stale_tf_multiplier+2) is None


def test_close_or_rollover_requests_canonical_resync_never_emits_a_trigger():
    live, now = setup()
    original = deepcopy(live.raw)
    closed = event(live, now); closed['result']['w']=True
    assert live.update(closed, now) == {'type':'resync'}
    end = live.raw['5m'][-1]['time']+300
    rollover=event(live, end); rollover['result']['t']=str(end)
    assert live.update(rollover, end) == {'type':'resync'}
    assert live.raw == original


def test_batch_calculates_once_for_all_latest_timeframes(monkeypatch):
    from app.services import mtf_live_preview as module
    live, now=setup()
    calls=[]
    original=module.forming_preview
    def counted(*args,**kwargs):
        calls.append(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'forming_preview',counted)
    messages=[event(live,now,105)]
    higher=event(live,now,108)
    higher['result'].update(n='1h_BTC_USDT',t=live.raw['1h'][-1]['time'])
    messages.append(higher)
    result=live.update_batch(messages,now)
    assert len(calls)==1
    assert result['candles']['5m']['close']==105
    assert result['candles']['1h']['close']==108
