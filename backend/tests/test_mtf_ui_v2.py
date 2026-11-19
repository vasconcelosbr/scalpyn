from copy import deepcopy
from unittest.mock import AsyncMock
import json
import pytest
from app.schemas.mtf_chart import MTFChartConfig, experimental_preset, presentation_preset
from app.services.mtf_chart_engine import zones, frame_zones, prepare_frame, evaluate, trend, TF_SECONDS
from app.services import mtf_chart_service as service
from app.services.profile_runtime_config import canonical_hash


def test_presentation_does_not_change_v1_identity_and_v2_requires_explicit_window():
    raw=experimental_preset()
    legacy={k:v for k,v in raw.items() if k not in {'pump_flow','presentation','zone_lookback_candles','atr_period','force_patterns','rejection_patterns','veto_filters'}}
    cfg=MTFChartConfig.model_validate(raw)
    assert canonical_hash(cfg.engine_dump())==canonical_hash(legacy)
    raw['presentation']=presentation_preset()
    assert MTFChartConfig.model_validate(raw).engine_dump()==cfg.engine_dump()
    raw['schema_version']='mtf_chart_v2'
    with pytest.raises(ValueError,match='ZONE_LOOKBACK_REQUIRED'): MTFChartConfig.model_validate(raw)


def test_zone_role_requires_crossing_whole_band_and_merged_touches_are_unique():
    raw=experimental_preset();raw.update(schema_version='mtf_chart_v2',zone_lookback_candles=raw['candle_limits'])
    cfg=MTFChartConfig.model_validate(raw)
    points=[{'time':i,'price':p,'kind':k,'confirmed_at':i+1} for i,p,k in [(1,100,'L'),(2,100.2,'L'),(1,100.3,'H'),(3,100.4,'H')]]
    result=zones(points,2,cfg,100.2)
    assert len(result)==1 and result[0]['type']=='TEST'
    assert result[0]['touches']==3
    assert result[0]['origins']==['RESISTANCE','SUPPORT']
    assert zones(points,2,cfg,102)[0]['type']=='SUPPORT'
    assert zones(points,2,cfg,98)[0]['type']=='RESISTANCE'


def test_zone_window_excludes_old_and_unconfirmed_pivots_and_merges_origins():
    raw=experimental_preset();raw.update(schema_version='mtf_chart_v2',zone_lookback_candles={tf:10 for tf in TF_SECONDS})
    cfg=MTFChartConfig.model_validate(raw)
    frame={'candles':[{'time':i*300} for i in range(50)],'atr':2,'pivots':[
        {'time':300,'price':100,'kind':'L','confirmed_at':600},
        {'time':42*300,'price':100,'kind':'L','confirmed_at':45*300},
        {'time':43*300,'price':100.2,'kind':'H','confirmed_at':46*300},
        {'time':49*300,'price':100.3,'kind':'L','confirmed_at':52*300}]}
    result=frame_zones(frame,'5m',cfg,102)
    assert len(result)==1
    assert result[0]['pivot_times']==[42*300,43*300]
    assert result[0]['touches']==2 and result[0]['type']=='SUPPORT'


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix_gap', [False, True])
async def test_display_warmup_covers_visible_series_without_changing_engine(monkeypatch, prefix_gap):
    now=14400*100
    raw=experimental_preset();raw['candle_limits']={tf:50 for tf in TF_SECONDS}
    cfg=MTFChartConfig.model_validate(raw)
    indicators={'ema':{'enabled':True,'periods':[9,21]},'atr':{'enabled':True,'period':14},'rsi':{'enabled':True,'period':14}}
    candles={tf:[{'time':now-(80-i)*seconds,'open':100+i*.1,'high':103+i*.1,'low':98+i*.1,'close':101+i*.1,'volume':100,'is_closed':True} for i in range(80)] for tf,seconds in TF_SECONDS.items()}
    if prefix_gap:
        for tf,cs in candles.items(): cs[0]['time'] -= TF_SECONDS[tf]
    async def fetch(symbol,tf,config,limit=None): return candles[tf][-(limit or config.candle_limits[tf]):]
    monkeypatch.setattr(service,'market_candles',fetch)
    monkeypatch.setattr(service,'profile_context',AsyncMock(return_value=(None,indicators,{},set(indicators),[])))
    monkeypatch.setattr(service,'calculation_window',lambda *args:30)
    monkeypatch.setattr(service.time,'time',lambda:now)
    from app.services.market_data_service import market_data_service
    monkeypatch.setattr(market_data_service,'fetch_orderbook_metrics',AsyncMock(return_value={'spread_pct':.01,'market_data_source':'gate.io'}))
    frames={tf:prepare_frame(cs[-50:],tf,now,indicators,set(indicators),cfg,30) for tf,cs in candles.items()}
    expected=evaluate('BTC_USDT',frames,cfg,now,.01,trend(frames['4h']['pivots']))
    result=await service.chart_data(object(),'user','BTC_USDT',None,cfg)
    from app.services.mtf_trigger_diagnostics import finalize_diagnostics
    # Diagnostic freshness/identity are finalized by the service, after engine evaluation.
    expected.update({key:result['analysis'][key] for key in ('observed_at_utc','config_hash','data_errors')})
    finalize_diagnostics(expected,cfg,now,now)
    for key,value in expected.items(): assert result['analysis'][key]==value,key
    for tf,frame in result['frames'].items():
        if prefix_gap:
            assert frame['warmup']['reason'] == 'CANDLE_GAP_OR_DUPLICATE'
        else:
            assert len(frame['indicators']['ema21'])==len(frame['candles'])
            assert frame['warmup']['reason'] is None
        assert frame['indicators']['ema21'][-1]==frames[tf]['indicators']['ema21'][-1]
    requested=[]
    async def observer_fetch(symbol,tf,config,limit=None):
        requested.append((tf,limit))
        return await fetch(symbol,tf,config,limit)
    monkeypatch.setattr(service,'market_candles',observer_fetch)
    observer=await service.chart_data(object(),'user','BTC_USDT',None,cfg,include_display=False)
    expected['observed_at_utc'] = observer['analysis']['observed_at_utc']
    expected['trigger_diagnostics']['observed_at_utc'] = observer['analysis']['observed_at_utc']
    for key,value in expected.items(): assert observer['analysis'][key]==value,key
    assert all(limit == cfg.candle_limits[tf] for tf,limit in requested)
    assert service.profile_context.await_args.args[-1] is False


def test_delta_is_owner_and_version_scoped_and_carries_overlay_deletions():
    service._chart_snapshots.clear()
    frame={'candles':[{'time':i,'close':i} for i in range(200)],'indicators':{'ema9':[{'time':i,'value':i} for i in range(200)]},
           'patterns':[{'time':1,'pattern':'DOJI'}],'pivots':[], 'zones':[{'low':1,'high':2}]}
    data={'symbol':'BTC_USDT','frames':{tf:deepcopy(frame) for tf in TF_SECONDS},'analysis':{'config_hash':'cfg','engine_version':'v1'},'as_of':'one'}
    full=service.chart_transport(data,context={'user':'a'},since=None,max_entries=10)
    later=deepcopy(data);later['as_of']='two'
    later['analysis']['trigger_diagnostics']={'status':'ARMED','checks':[{'actual':.000001,'expected':.000002}]}
    for f in later['frames'].values(): f['candles'][-1]['close']=999;f['patterns']=[]
    delta=service.chart_transport(later,context={'user':'a'},since=full['cursor'],max_entries=10)
    assert delta['transport']=='delta'
    assert delta['analysis']['trigger_diagnostics']==later['analysis']['trigger_diagnostics']
    assert delta['frames']['5m']['candles']==[{'time':199,'close':999}]
    assert delta['frames']['5m']['patterns_removed']==[[1,'DOJI']]
    assert 'zones' not in delta['frames']['5m']
    assert len(json.dumps(delta))<len(json.dumps(full))
    assert service.chart_transport(later,context={'user':'b'},since=full['cursor'],max_entries=10)['transport']=='full'
    later['analysis']['config_hash']='changed'
    assert service.chart_transport(later,context={'user':'a'},since=full['cursor'],max_entries=10)['transport']=='full'
    later['analysis']['config_hash']='cfg'
    for f in later['frames'].values():
        for c in f['candles']: c['time']+=10000
    assert service.chart_transport(later,context={'user':'a'},since=full['cursor'],max_entries=10)['transport']=='full'
