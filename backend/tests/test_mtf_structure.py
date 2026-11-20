from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, UUID

import pytest
from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.services.mtf_chart_engine import evaluate, iso
from app.services.mtf_structure import structure_context, enrich_watchlist
from test_mtf_chart_engine import setup_frames


def fixture():
    cfg = MTFChartConfig.model_validate(experimental_preset())
    frames = setup_frames()
    analysis = evaluate('TEST_USDT', frames, cfg, 28800, .01, 'UP')
    analysis['config_hash'] = 'fixture'
    frames['4h']['indicators'] = {'ema50':[{'time':14400,'value':98}]}
    for frame in frames.values():
        frame['candles'] = [{**c,'closed':True} for c in frame['candles']]
    frames['4h']['candles'].append({'time':28800,'open':100,'close':90,'high':101,'low':89,'closed':False})
    return cfg, {'as_of':iso(28801),'analysis':analysis,'frames':frames}


def test_projection_preserves_entire_evaluation_and_separates_open_candle():
    cfg, data = fixture()
    before = deepcopy(data)
    context = structure_context(data,cfg,now=28802)
    assert data == before
    assert context['frames']['4h']['trend'] == data['analysis']['context_4h']['trend']
    assert context['frames']['4h']['extension_atr'] == (102 - 98) / 2
    assert context['frames']['4h']['live_change_pct'] == pytest.approx(-10)
    assert context['frames']['4h']['last_pivot_age_candles'] == 1
    assert context['invalidation']['label'] == 'HL'
    assert context['invalidation_distance_atr'] == (100 - 95) / 2
    assert context['operational_effect'] is False


def test_missing_series_future_pivots_and_expired_context_are_not_estimated():
    cfg, data = fixture()
    data['frames']['4h']['indicators'] = {}
    data['frames']['4h']['pivots'].append({'time':30000,'confirmed_at':31000,'price':1000,'label':'HL','kind':'L'})
    context = structure_context(data,cfg,now=28802)
    assert context['frames']['4h']['extension_atr'] is None
    assert context['invalidation']['price'] == 95
    assert context['frames']['5m']['trend'] is None
    assert context['conflict'] is None
    assert structure_context(data,cfg,now=28800+cfg.stale_tf_multiplier*300) is None
    data['frames']['4h']['atr'] = None
    assert structure_context(data,cfg,now=28802)['invalidation_distance_atr'] is None


@pytest.mark.parametrize('direction,state', [('UP','outflow'),('DOWN','inflow')])
def test_conflict_uses_existing_categories_in_both_directions(direction,state):
    cfg,data=fixture()
    data['analysis']['context_4h']['trend']=direction
    data['analysis']['pump_flow']={'capital':{'1h':{'state':state,'ratio':-.0304}}}
    before=deepcopy(data['analysis'])
    context=structure_context(data,cfg,now=28802)
    assert context['conflict'] is True
    assert context['conflicts'] == [{'kind':'flow','timeframe':'1h','state':state,'ratio':-.0304,'sequence':[]}]
    assert data['analysis']==before
    data['analysis']['pump_flow']['capital']['1h']['state']='unavailable'
    assert structure_context(data,cfg,now=28802)['conflict'] is None


def test_structure_conflict_reuses_classifier_and_preserves_h4_label():
    cfg,data=fixture()
    data['frames']['5m']['pivots']=[{'time':i,'confirmed_at':i+1,'price':price,'label':label,'kind':kind}
        for i,price,label,kind in [(0,100,'H','H'),(1,90,'L','L'),(2,95,'LH','H'),(3,85,'LL','L')]]
    context=structure_context(data,cfg,now=28802)
    assert context['frames']['5m']['trend']=='DOWN'
    assert context['frames']['4h']['trend']=='UP'
    assert context['conflict'] is True
    assert context['conflicts'][0]['kind']=='structure'


@pytest.mark.asyncio
async def test_watchlist_reads_exact_validated_owner_observation_without_writes(monkeypatch):
    cfg,data=fixture();oid=str(uuid4());uid=uuid4()
    item={'symbol':'TEST_USDT','mtf':data['analysis'],'mtf_observation':{'observation_id':oid}}
    snapshot={'analysis':deepcopy(data['analysis']),'observed_at':data['as_of'],
        'frames':{tf:{**f,'last_closed_candle':next(c for c in reversed(f['candles']) if c['closed'])} for tf,f in data['frames'].items()},'indicators':[]}
    db=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:[{'id':oid,'payload':snapshot}]))))
    import app.services.mtf_structure as service
    real=service.structure_context
    monkeypatch.setattr(service,'structure_context',lambda data,cfg,**kwargs:real(data,cfg,now=28802,**kwargs))
    before=deepcopy(item)
    result=await enrich_watchlist(db,uid,'l3-consolidated',[item],cfg)
    assert result[0]['structure_context']['source']=='observation'
    assert item==before
    sql,params=db.execute.await_args.args
    assert str(sql).lstrip().startswith('SELECT')
    assert params=={'uid':uid,'wid':'l3-consolidated','ids':[UUID(oid)]}
    snapshot['analysis']['config_hash']='different'
    assert (await enrich_watchlist(db,uid,'l3-consolidated',[item],cfg))[0]['structure_context'] is None
