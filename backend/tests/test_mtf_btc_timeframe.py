"""BTC timeframe changes selection only; legacy identity and trading rules stay intact."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
from app.schemas.mtf_history import HistoryConfig
from app.services import mtf_chart_service as chart, mtf_pump_flow
from app.services.mtf_btc_context import load_btc_context, frozen_frame
from app.services.mtf_chart_engine import TF_SECONDS, evaluate, iso, trend
from app.services.mtf_history import snapshot
from app.services.profile_runtime_config import canonical_hash
from test_mtf_chart_engine import setup_frames

NOW = 14400 * 100


def config(tf=None):
    data = experimental_preset()
    data.pop("btc_trend_timeframe", None)
    if tf is not None:
        data["btc_trend_timeframe"] = tf
    return MTFChartConfig.model_validate(data)


def bars(tf, direction="RANGE", count=80):
    shape = [0, 2, 5, 2, 0, -2, -5, -2]
    return [dict(time=NOW-(count-i)*TF_SECONDS[tf], open=(v:=120+shape[i%8]-(i*.2 if direction=="DOWN" else 0)),
                 close=v+.1, high=v+1, low=v-1, volume=100, is_closed=True)
            for i in range(count)]


@pytest.fixture
def chart_setup(monkeypatch):
    profile = SimpleNamespace(id=uuid4(), config={})
    indicators = {"atr":{"enabled":True,"period":14}}
    monkeypatch.setattr(chart, "profile_context", AsyncMock(return_value=(profile,indicators,{},{"atr"},[])))
    monkeypatch.setattr(chart, "calculation_window", lambda *args:50)
    monkeypatch.setattr(chart.time, "time", lambda:NOW)
    monkeypatch.setattr(mtf_pump_flow, "load_context", AsyncMock(return_value=None))
    from app.services.market_data_service import market_data_service
    monkeypatch.setattr(market_data_service, "fetch_orderbook_metrics",
                        AsyncMock(return_value={"spread_pct":.01,"market_data_source":"gate.io"}))
    async def fetch(symbol,tf,cfg,limit=None):
        return bars(tf, "DOWN" if symbol=="BTC_USDT" and tf=="4h" else "RANGE")
    mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(chart,"market_candles",mock)
    chart._frame_cache.clear()
    return profile, indicators, mock


@pytest.mark.asyncio
@pytest.mark.parametrize("tf", [None,"4h","1h"])
async def test_real_chart_freezes_both_states_and_legacy_decision_parity(chart_setup, tf):
    profile,indicators,fetch=chart_setup
    cfg=config(tf)
    data=await chart.chart_data(object(),uuid4(),"ALT_USDT",profile.id,cfg,include_display=False)
    a=data["analysis"]; context=a["btc_context"]
    assert context["frames"]["4h"]["trend"]=="DOWN"
    assert context["frames"]["1h"]["trend"]=="RANGE"
    selected=context["frames"][cfg.btc_trend_timeframe]["trend"]
    legacy=evaluate("ALT_USDT",data["frames"],cfg,NOW,.01,"DOWN")
    expected=evaluate("ALT_USDT",data["frames"],cfg,NOW,.01,selected)
    for key in ("score","vetoes","status","plan"):
        assert a[key]==expected[key]
        if tf in (None,"4h"):
            assert a[key]==legacy[key]
    frozen=snapshot(data,{},cfg,HistoryConfig(),iso(NOW))
    assert frozen["analysis"]["btc_context"]==context
    for frame in context["frames"].values():
        assert len(frame["pivots_used"])==4
        assert all(p["confirmed_at"]<=NOW for p in frame["pivots_used"])
        assert frame["reference_candle"]["close_at"]==iso(NOW)
        assert frame["candle_count"]==80
    btc_calls=[c for c in fetch.await_args_list if c.args[0]=="BTC_USDT"]
    assert sorted(c.args[1] for c in btc_calls)==["1h","4h"]
    check=next(c for c in a["trigger_diagnostics"]["checks"] if c["id"]=="btc_downtrend")
    assert check["timeframe"]==cfg.btc_trend_timeframe and check["operator"]=="!="


@pytest.mark.asyncio
async def test_hourly_selection_changes_only_btc_gate(chart_setup):
    _,indicators,_=chart_setup
    cfg=config("1h")
    ctx=await load_btc_context("ALT_USDT",{},NOW,indicators,cfg,chart.market_candles,chart.prepared)
    frames=setup_frames()
    before=evaluate("ALT_USDT",frames,cfg,28800,.01,ctx["frames"]["4h"]["trend"])
    after=evaluate("ALT_USDT",frames,cfg,28800,.01,ctx["frames"]["1h"]["trend"])
    assert before["vetoes"]==["BTC_DOWNTREND"] and before["status"]=="NO_TRADE"
    assert after["vetoes"]==[] and after["status"]=="TRIGGERED"
    assert before["score"]["raw"]==after["score"]["raw"] and before["plan"]==after["plan"]
    frame=await chart.prepared(bars("1h","DOWN"),"1h",NOW,indicators,{"atr"},cfg)
    assert trend(frame["pivots"])=="DOWN"
    blocked=evaluate("ALT_USDT",frames,cfg,28800,.01,trend(frame["pivots"]))
    assert blocked["vetoes"]==["BTC_DOWNTREND"]
    assert "BTC_DOWNTREND" not in evaluate("BTC_USDT",frames,cfg,28800,.01,"DOWN")["vetoes"]


@pytest.mark.asyncio
async def test_btc_watchlist_reuses_asset_frames_without_extra_fetch(chart_setup):
    profile,_,fetch=chart_setup
    data=await chart.chart_data(object(),uuid4(),"BTC_USDT",profile.id,config("1h"),include_display=False)
    assert len(fetch.await_args_list)==3
    assert data["analysis"]["btc_context"]["exempt"]
    assert "BTC_DOWNTREND" not in data["analysis"]["vetoes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("count,expected", [(0,None),(1,"RANGE"),(6,"RANGE")])
async def test_insufficient_hourly_data_keeps_existing_missing_vs_few_pivots_policy(chart_setup,count,expected):
    _,indicators,_=chart_setup
    cfg=config("1h")
    frame=await chart.prepared(bars("1h",count=count),"1h",NOW,indicators,{"atr"},cfg)
    reading=frozen_frame(frame,"1h",NOW,cfg)
    assert reading["trend"]==expected
    result=evaluate("ALT_USDT",setup_frames(),cfg,28800,.01,expected)
    assert ("BTC_CONTEXT_UNAVAILABLE" in result["vetoes"])==(expected is None)


@pytest.mark.asyncio
async def test_gap_and_stale_context_and_optional_failure(chart_setup):
    _,indicators,_=chart_setup
    cfg=config()
    async def fetch(symbol,tf,cfg):
        if tf=="1h": raise RuntimeError("optional unavailable")
        return bars(tf,"DOWN")
    ctx=await load_btc_context("ALT_USDT",{},NOW,indicators,cfg,fetch,chart.prepared)
    assert ctx["frames"]["4h"]["trend"]=="DOWN" and ctx["frames"]["1h"]["trend"] is None
    raw=bars("1h");raw.pop(10)
    async def gap(symbol,tf,cfg):return raw if tf=="1h" else bars(tf)
    ctx=await load_btc_context("ALT_USDT",{},NOW,indicators,config("1h"),gap,chart.prepared)
    assert ctx["frames"]["1h"]["trend"] is None
    assert ctx["frames"]["1h"]["reason"]=="CANDLE_GAP_OR_DUPLICATE"
    frame={"candles":bars("1h")[:-4],"pivots":[]}
    assert frozen_frame(frame,"1h",NOW,cfg)["trend"] is None


def test_legacy_identity_l3_validation_and_configuration_rollback():
    from test_l3_armed_observation import context
    from app.services.l3_armed_observation import reading
    legacy=config();explicit=config("4h");hourly=config("1h")
    assert "btc_trend_timeframe" not in legacy.engine_dump()
    assert canonical_hash(legacy.engine_dump())==canonical_hash(explicit.engine_dump())
    assert canonical_hash(hourly.engine_dump())!=canonical_hash(legacy.engine_dump())
    _,_,_,item,row,kwargs=context()
    before=deepcopy(row)
    row["analysis"].pop("btc_context",None)
    kwargs["cfg"]=legacy
    assert reading(item,row,**kwargs)["data_state"]=="current"
    kwargs["cfg"]=hourly
    assert "CONFIG_CHANGED" in reading(item,row,**kwargs)["reason_codes"]
    kwargs["cfg"]=explicit
    assert reading(item,row,**kwargs)["data_state"]=="current"
    assert row==before
    restored=MTFChartConfig.model_validate({**hourly.model_dump(),"btc_trend_timeframe":"4h"})
    assert restored.engine_dump()==legacy.engine_dump()
    with pytest.raises(ValueError):config("5m")

@pytest.mark.asyncio
async def test_market_cache_uses_gate_only_configured_hourly_limit_and_excludes_forming(monkeypatch):
    import pandas as pd
    from app.services.market_data_service import market_data_service
    cfg=config("1h")
    cfg.candle_limits["1h"]=150
    rows=bars("1h")
    rows.append({**rows[-1],"time":NOW,"high":99999,"is_closed":False})
    df=pd.DataFrame(rows)
    df["time"]=pd.to_datetime(df["time"],unit="s",utc=True)
    df.attrs["exchange"]="gate.io"
    fetch=AsyncMock(return_value=df)
    monkeypatch.setattr(market_data_service,"fetch_ohlcv",fetch)
    chart._market_cache.clear()
    first=await chart.market_candles("BTC_USDT","1h",cfg)
    second=await chart.market_candles("BTC_USDT","1h",cfg)
    fetch.assert_awaited_once_with("BTC_USDT","1h",limit=150,gate_only=True)
    assert first==second
    monkeypatch.setattr(chart,"calculation_window",lambda *args:50)
    prepared=await chart.prepared(first,"1h",NOW,{"atr":{"enabled":True,"period":14}},{"atr"},cfg)
    frozen=frozen_frame(prepared,"1h",NOW,cfg)
    assert frozen["candle_count"]==80 and frozen["reference_candle"]["close_at"]==iso(NOW)
    assert all(p["confirmed_at"]<=NOW for p in frozen["pivots_used"])
    chart._market_cache.clear()


