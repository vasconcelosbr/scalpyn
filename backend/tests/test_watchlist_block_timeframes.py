"""Regression: flattened watchlist reads must not erase exact candle block inputs."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.block_condition_timeframe import prepare_block_candle_inputs
from app.services.pipeline_rejections import (
    build_asset_evaluation_trace, build_trace_asset, evaluate_rejections, recompute_rejection_trace,
)


def config(indicator='adx_slope_3', timeframe='15m'):
    return {'block_rules': {'blocks': [{'name': 'guard', 'conditions': [
        {'indicator': indicator, 'source': 'ohlcv', 'timeframe': timeframe,
         'operator': '<', 'value': 0, 'max_age_seconds': 900}]}]}}


def merged(indicator, timeframe, actual, **metadata):
    c = {'indicator': indicator, 'timeframe': timeframe, 'actual': actual,
         'computed_at': '2026-09-28T22:00:00Z', 'age_seconds': 10,
         'candle_closed': True, 'stale': False, **metadata}
    return SimpleNamespace(candidates=[c], as_flat_dict=lambda: {indicator: actual})


@pytest.mark.asyncio
@pytest.mark.parametrize('level', ['L1', 'L2', 'L3'])
@pytest.mark.parametrize('market', ['spot', 'futures'])
@pytest.mark.parametrize('indicator,timeframe', [('adx_slope_3','15m'), ('bb_upper_distance_pct','15m'),
                                               ('macd_hist_slope_3','15m'), ('rsi_6','1m')])
async def test_watchlist_levels_refresh_and_read_use_exact_candles(monkeypatch, level, market, indicator, timeframe):
    from app.services import indicators_provider as provider
    cfg=config(indicator,timeframe); before=deepcopy(cfg)
    values=merged(indicator,timeframe,-1)
    fetch=AsyncMock(return_value={'TEST_USDT': values})
    direct=AsyncMock(return_value={'TEST_USDT': values.candidates[0]})
    monkeypatch.setattr(provider, 'get_timeframe_indicators', fetch)
    monkeypatch.setattr(provider, 'get_closed_block_rsi6', direct)
    # Opposite flat value must never win over a declared candle timeframe.
    flat=build_trace_asset('TEST_USDT', indicators={indicator: 99})
    assert build_asset_evaluation_trace(flat,profile_config=cfg)[0]['status']=='SKIPPED'
    prepared=await prepare_block_candle_inputs(object(),[{**flat,'is_futures':market=='futures'}],cfg)
    approved,rejected=evaluate_rejections(prepared,profile_config=cfg,stage=level,profile_id='fixture')
    assert not approved and rejected[0]['stage']==level
    assert rejected[0]['evaluation_trace'][0]['current_value']==-1
    for indicators in ({indicator:99}, None):
        trace=recompute_rejection_trace('TEST_USDT',profile_config=cfg,indicators=indicators,
                                        meta={},block_inputs=prepared[0],stored_trace=[])
        assert trace[0]['outcome']=='TRIPPED' and trace[0]['current_value']==-1
    if timeframe=='1m' and market=='spot':
        direct.assert_awaited_once(); fetch.assert_not_awaited()
    else:
        assert fetch.await_args.kwargs=={'timeframe':timeframe,'market_type':market}
    assert cfg==before


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [{'stale':True},{'age_seconds':901},{'candle_closed':False}])
async def test_unusable_candles_do_not_become_valid_through_watchlist_fallback(monkeypatch,bad):
    from app.services import indicators_provider as provider
    monkeypatch.setattr(provider,'get_timeframe_indicators',AsyncMock(return_value={
        'TEST_USDT':merged('adx_slope_3','15m',-1,**bad)}))
    prepared=await prepare_block_candle_inputs(object(),[{'symbol':'TEST_USDT'}],config())
    trace=recompute_rejection_trace('TEST_USDT',profile_config=config(),indicators={'adx_slope_3':-1},
                                    meta={},block_inputs=prepared[0])
    assert trace[0]['status']=='SKIPPED' and trace[0]['current_value'] is None


def test_watchlist_api_wires_candle_preparation_into_all_mutable_trace_paths():
    """Guard the omitted API call sites, not only the shared evaluator."""
    path=Path(__file__).parents[1]/'app/api/watchlists.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    funcs={f.name:f for f in tree.body if isinstance(f,ast.AsyncFunctionDef)}
    for name in ('_resolve_and_persist','get_watchlist_assets','_get_watchlist_rejections_payload'):
        calls=[n for n in ast.walk(funcs[name]) if isinstance(n,ast.Call)]
        assert any(isinstance(c.func,ast.Name) and c.func.id=='prepare_block_candle_inputs' for c in calls)
        if name=='get_watchlist_assets':
            traces=[c for c in calls if isinstance(c.func,ast.Name) and c.func.id=='build_asset_evaluation_trace']
            assert traces and all('block_inputs' in ast.unparse(c.args[0]) for c in traces)
        if name=='_get_watchlist_rejections_payload':
            call=next(c for c in calls if isinstance(c.func,ast.Name) and c.func.id=='recompute_rejection_trace')
            assert any(k.arg=='block_inputs' for k in call.keywords)
    # Spot L3 approvals must retain frozen authorization, never live recomputation.
    source=ast.unparse(funcs['get_watchlist_assets'])
    # 2026-10-06: the persisted-authority read is classify_live_l3_for_display.
    assert source.index('classify_live_l3_for_display') < source.index('prepare_block_candle_inputs')
