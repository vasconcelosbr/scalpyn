from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import json
import math

import pytest

from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.schemas.mtf_history import HistoryConfig
from app.services import mtf_history as history
from app.services.mtf_chart_engine import evaluate, indicator_history, iso, TF_SECONDS
from app.services.mtf_structure import structure_context
from app.services.mtf_structure_indicators import (
    enrich_observer_frames, STRUCTURE_INDICATOR_CONFIG, STRUCTURE_INDICATOR_VERSION,
)
from test_mtf_chart_engine import setup_frames, candle


def sample(state='TRIGGERED', count=60, window=50):
    cfg = MTFChartConfig.model_validate(experimental_preset())
    frames = setup_frames()
    for tf, frame in frames.items():
        last = frame['candles'][-1]
        # Preserve the trigger/zone fixture, adding a deterministic closed prefix.
        prefix = [dict(candle(t=last['time']-(count-1-i)*TF_SECONDS[tf],
                              c=100+math.sin(i)), closed=True) for i in range(count-1)]
        frame['candles'] = prefix + [dict(last, closed=True)]
        frame['indicator_sample_count'] = window
        frame['indicators'] = {'atr': [{'time':last['time'], 'value':frame['atr']}]}
    if state in ('ARMED', 'WAIT'):
        frames['5m']['patterns'] = []
    if state == 'WAIT':
        frames['1h']['pivots'] = []
    analysis = evaluate('TEST_USDT', frames, cfg, 28800, .01, 'DOWN' if state == 'NO_TRADE' else 'UP')
    analysis.update(observed_at_utc=iso(28801), config_hash='unchanged-config',
                    profile_hash='unchanged-profile', indicator_identity={'config_hash':'unchanged-indicators'},
                    unavailable_profile_indicators=[])
    return cfg, {'frames':frames, 'analysis':analysis, 'as_of':iso(28801),
                 'indicator_config':{'atr':{'enabled':True,'period':14}},
                 'quote':{'price':100,'received_at':iso(28801)}}


@pytest.mark.parametrize('state', ['TRIGGERED', 'ARMED', 'WAIT', 'NO_TRADE'])
def test_observer_enrichment_preserves_entire_decision_and_snapshot_identity(state):
    cfg, data = sample(state)
    if state == 'NO_TRADE':
        # Display enrichment cannot repair a required profile input retroactively.
        data['analysis']['unavailable_profile_indicators'] = ['ema50']
    before = deepcopy(data)
    old = history.snapshot(data, {}, cfg, HistoryConfig(), iso(28800))
    enrich_observer_frames(data)
    new = history.snapshot(data, {}, cfg, HistoryConfig(), iso(28800))
    assert data['analysis'] == before['analysis']
    assert data['analysis']['status'] == state
    assert new['analysis'] == old['analysis']  # raw/total, vetoes, status, plan and hashes
    assert new['quality'] == old['quality']
    assert new['data_reasons'] == old['data_reasons']
    if state == 'NO_TRADE':
        assert new['quality'] == 'pending'
        assert 'PROFILE_INDICATORS_UNAVAILABLE' in new['data_reasons']
    assert evaluate('TEST_USDT', data['frames'], cfg, 28800, .01,
                    'DOWN' if state == 'NO_TRADE' else 'UP') == evaluate(
        'TEST_USDT', before['frames'], cfg, 28800, .01, 'DOWN' if state == 'NO_TRADE' else 'UP')
    for tf, frame in data['frames'].items():
        expected = indicator_history(before['frames'][tf]['candles'], STRUCTURE_INDICATOR_CONFIG, {'ema'}, window=50)
        assert frame['indicators']['ema50'] == expected['ema50'][-1:]
        assert frame['atr'] == before['frames'][tf]['atr']
        point = next(p for p in new['indicators'] if p['timeframe']==tf and p['name']=='ema50')
        assert point['role'] == 'structural_display_only'
        assert point['configuration_identity'] == {'version':STRUCTURE_INDICATOR_VERSION,'sample_count':50}
    # Repeated enrichment cannot overwrite an existing producer's sample.
    frozen = deepcopy(data)
    enrich_observer_frames(data)
    assert data == frozen


@pytest.mark.parametrize('count,window', [(49,50), (199,200), (60,30)])
def test_short_history_keeps_null_extension_and_list_readable(count,window):
    cfg,data = sample(count=count,window=window)
    enrich_observer_frames(data)
    assert all('ema50' not in f['indicators'] for f in data['frames'].values())
    context = structure_context(data,cfg,now=28802)
    assert context['frames']['4h']['extension_atr'] is None
    assert history.snapshot(data,{},cfg,HistoryConfig(),iso(28800))['quality']=='current'


def test_forming_candle_is_excluded_and_missing_atr_is_not_fabricated():
    cfg,data = sample()
    original = deepcopy(data)
    for tf,frame in data['frames'].items():
        frame['candles'].append(dict(candle(t=28800,c=999999),closed=False))
    enrich_observer_frames(original)
    enrich_observer_frames(data)
    for tf in data['frames']:
        assert data['frames'][tf]['indicators']==original['frames'][tf]['indicators']
    data['frames']['4h']['atr']=None
    assert structure_context(data,cfg,now=28802)['frames']['4h']['extension_atr'] is None


def test_incomplete_legacy_frame_without_producer_metadata_is_unchanged():
    from test_mtf_history import sample as legacy_sample
    data = legacy_sample()
    before = deepcopy(data)
    enrich_observer_frames(data)
    assert data == before


@pytest.mark.asyncio
async def test_old_observation_without_ema_passes_l3_validation_and_projection(monkeypatch):
    from test_l3_armed_observation import context
    from app.services import l3_armed_observation, mtf_structure
    uid,_,_,item,row,kwargs = context()
    cfg,data = sample('ARMED')
    # Same legacy payload, no new display metadata or EMA series required.
    row['analysis'].update(data['analysis'])
    row['analysis'].update(config_hash=l3_armed_observation.canonical_hash(kwargs['cfg'].engine_dump()),
        profile_hash=kwargs['profile_hashes'][item['profile_id']],indicator_identity=kwargs['indicator_identity'],
        candle_close_at_utc=kwargs['now'].isoformat())
    validated = l3_armed_observation.reading(item,row,**kwargs)
    assert validated['data_state']=='current' and validated['reason_codes']==[]
    item.update(mtf=data['analysis'], mtf_observation=validated)
    payload = history.snapshot(data,{},cfg,HistoryConfig(),iso(28800))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        mappings=lambda:SimpleNamespace(all=lambda:[{'id':row['id'],'payload':payload}]))))
    real = mtf_structure.structure_context
    monkeypatch.setattr(mtf_structure,'structure_context',lambda d,c,**kw:real(d,c,now=28802,**kw))
    result = await mtf_structure.enrich_watchlist(db,uid,'l3-consolidated',[item],cfg)
    assert result[0]['structure_context']['frames']['4h']['extension_atr'] is None


@pytest.mark.asyncio
async def test_capture_persists_structural_ema_for_profile_without_ema(monkeypatch):
    cfg,data = sample()
    before = deepcopy(data['analysis'])
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[True,None]), commit=AsyncMock(),
        execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:[]))))
    monkeypatch.setattr(history.chart,'load_config',AsyncMock(return_value=cfg))
    monkeypatch.setattr(history.chart,'chart_data',AsyncMock(return_value=data))
    monkeypatch.setattr(history.config_service,'get_config',AsyncMock(return_value=HistoryConfig().model_dump()))
    import httpx
    client = AsyncMock()
    client.get.return_value = SimpleNamespace(raise_for_status=lambda:None,json=lambda:[{'last':'100'}])
    client.__aenter__.return_value=client
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client)
    assert await history.capture(db,uuid4(),'l3-consolidated',{'symbol':'TEST_USDT','profile_id':str(uuid4())},iso(28800))
    payload = next(json.loads(call.args[1]['payload']) for call in db.execute.await_args_list
                   if 'INSERT INTO mtf_history_observations' in str(call.args[0]))
    assert payload['analysis']==before
    assert {p['timeframe'] for p in payload['indicators'] if p['name']=='ema50'}==set(cfg.active_timeframes())
    assert history.chart.chart_data.await_args.kwargs=={'include_display':False}


@pytest.mark.asyncio
async def test_real_chart_observer_frames_use_market_closed_flag(monkeypatch):
    from app.services import mtf_chart_service as chart, mtf_pump_flow
    from app.services.market_data_service import market_data_service
    cfg = MTFChartConfig.model_validate(experimental_preset())
    now = 14400*100
    profile = SimpleNamespace(id=uuid4(),config={'default_timeframe':'4h'})
    config = {'atr':{'enabled':True,'period':14},'ema':{'enabled':True,'periods':[50]}}
    monkeypatch.setattr(chart,'profile_context',AsyncMock(return_value=(profile,config,{}, {'atr'},['atr'])))
    monkeypatch.setattr(chart,'calculation_window',lambda *args:50)
    monkeypatch.setattr(chart.time,'time',lambda:now)
    async def fetch(symbol,tf,cfg,limit=None):
        return [dict(candle(t=now-(60-i)*TF_SECONDS[tf],c=100+math.sin(i)),is_closed=True)
                for i in range(60)]
    monkeypatch.setattr(chart,'market_candles',fetch)
    monkeypatch.setattr(market_data_service,'fetch_orderbook_metrics',AsyncMock(
        return_value={'spread_pct':.01,'market_data_source':'gate.io'}))
    monkeypatch.setattr(mtf_pump_flow,'load_context',AsyncMock(return_value=None))
    data = await chart.chart_data(object(),uuid4(),'BTC_USDT',profile.id,cfg,include_display=False)
    assert all('ema50' not in f['indicators'] for f in data['frames'].values())
    assert all(c['is_closed'] and 'closed' not in c for f in data['frames'].values() for c in f['candles'])
    before = deepcopy(data['analysis'])
    enrich_observer_frames(data)
    payload = history.snapshot(data,{},cfg,HistoryConfig(),iso(now))
    assert payload['analysis']==before
    assert {p['timeframe'] for p in payload['indicators'] if p['name']=='ema50'}==set(cfg.active_timeframes())
