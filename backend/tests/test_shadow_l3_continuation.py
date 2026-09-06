"""Synthetic scenarios, never calibration recommendations."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.shadow_l3_exit_policy import ShadowL3ExitPolicy, frozen_policy
from app.services.shadow_l3_exit_evaluator import advance
from app.services.shadow_l3_exit_service import build_evidence
from app.services.shadow_l3_flow_capture import normalize_trade

T = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def policy():
    return ShadowL3ExitPolicy(
        flow_window_seconds=120,cvd_window_seconds=120,continuation_taker_min=.6,
        continuation_delta_min=.2,continuation_cvd_min=.2,continuation_price_min_pct=.1,
        weakening_taker_max=.45,weakening_delta_max=-.1,weakening_cvd_max=-.1,
        confirmation_seconds=120,alignment_seconds=60,pivot_left=1,pivot_right=1,
        initial_buffer_pct=.5,step_trigger_pct=1,step_floor_pct=.8,atr_period=2,
        atr_multiplier=2,tight_atr_multiplier=1,max_age_seconds=120,min_coverage_pct=50,
        max_gap_seconds=90,warmup_seconds=120)


def ev(at, weak=False, lost=False, valid=True):
    return dict(quality="VALID" if valid else "UNAVAILABLE",decision_at=(at+timedelta(minutes=1)).isoformat(),
                taker_ratio=.3 if weak else .8,delta_normalized=-.4 if weak else .6,
                cvd_slope_normalized=-.4 if weak else .6,price_return_pct=.5,atr=2,
                cvd=5,support_price=105,structure_lost=lost)


def step(policy,state=None,i=0,op=100,hi=101,lo=100,close=101,weak=False,lost=False,valid=True,trailing=None):
    at=T+timedelta(minutes=i)
    return advance(state or {},dict(time=at,open=op,high=hi,low=lo,close=close),ev(at,weak,lost,valid),policy,
                   entry=100,tp=102,sl=95,trailing=trailing or {},entry_at=T)


def test_default_observation_has_no_economic_defaults():
    p=ShadowL3ExitPolicy()
    assert p.mode=="OBSERVE" and p.initial_buffer_pct is None
    assert p.missing_parameters()
    with pytest.raises(ValidationError): ShadowL3ExitPolicy(mode="APPLY")


def test_hash_includes_parameters_not_mode(policy):
    assert policy.digest()==policy.model_copy(update={"mode":"APPLY"}).digest()
    assert policy.digest()!=policy.model_copy(update={"initial_buffer_pct":.7}).digest()
    assert frozen_policy({})["config"]["mode"]=="OBSERVE"


def test_validation_rejects_looser_tightening_and_nan(policy):
    for update in ({"tight_atr_multiplier":3},{"step_floor_pct":2},{"weakening_taker_max":.9},{"initial_buffer_pct":float('nan')}):
        with pytest.raises(ValidationError): ShadowL3ExitPolicy.model_validate({**policy.model_dump(),**update})


def test_tp_without_valid_persistent_flow(policy):
    assert step(policy,hi=103,close=102,valid=False)["outcome"]=="TP_HIT"
    assert step(policy,hi=103,close=102)["outcome"]=="TP_HIT"


def test_floor_monotonic_no_profit_cap(policy):
    s=step(policy)
    s=step(policy,s,i=1,op=101,lo=101,hi=101,close=101)
    s=step(policy,s,i=2,op=101,lo=101,hi=104,close=104)
    assert s["continuation"] and s["floor_price"]<104
    for i,price in enumerate((108,115,130,140),3):
        old=s["floor_price"]
        s=step(policy,s,i=i,op=price,lo=price,hi=price+1,close=price)
        assert not s.get("outcome") and s["floor_price"]>=old


def test_prior_floor_wins_over_strong_flow_and_gap(policy):
    s=dict(continuation=True,floor_price=105,high_water_mark=110,last_candle_at=T.isoformat())
    out=step(policy,s,i=1,op=103,lo=102,hi=112,close=111)
    assert out["outcome"]=="TRAILING_STOP" and out["exit_price"]==103
    assert out["trigger_price"]==105


def test_weakening_requires_price_structure_and_persistence(policy):
    s=dict(continuation=True,floor_price=101,high_water_mark=106,last_candle_at=T.isoformat())
    s=step(policy,s,i=1,op=106,lo=105,hi=106,close=106,weak=True,lost=True)
    assert not s.get("outcome")
    s=step(policy,s,i=2,op=106,lo=105,hi=106,close=106,weak=True,lost=False)
    assert not s.get("outcome") and s["state"]=="TIGHTENED"
    s=step(policy,s,i=3,op=106,lo=105,hi=106,close=106,weak=True,lost=True)
    assert s["pending_exit"]["outcome"]=="FLOW_STRUCTURE_EXIT"
    s=step(policy,s,i=4,op=106,lo=106,hi=107,close=107)
    assert s["outcome"]=="FLOW_STRUCTURE_EXIT" and s["exit_price"]==106


def test_missing_data_keeps_protection_resets_persistence(policy):
    s=dict(continuation=True,floor_price=101,high_water_mark=106,weak_seconds=120,last_candle_at=T.isoformat())
    out=step(policy,s,i=1,op=106,lo=105,hi=110,close=109,weak=True,lost=True,valid=False)
    assert not out.get("outcome") and out["floor_price"]==101 and out["weak_seconds"]==0


def test_new_floor_does_not_fill_retroactively(policy):
    s=dict(continuation=True,floor_price=101,high_water_mark=106,last_candle_at=T.isoformat())
    out=step(policy,s,i=1,op=106,lo=105,hi=115,close=106)
    assert out["pending_exit"]["outcome"]=="TRAILING_STOP" and not out.get("outcome")
    out=step(policy,out,i=2,op=105,lo=104,hi=107,close=106)
    assert out["outcome"]=="TRAILING_STOP" and out["exit_price"]==105


def test_duplicate_restart_terminal_and_partial_entry(policy):
    s=step(policy)
    assert step(policy,s)==s
    import json
    restored=json.loads(json.dumps(s))
    assert step(policy,restored,i=1,hi=104,lo=101,close=104)==step(policy,s,i=1,hi=104,lo=101,close=104)
    out=step(policy,hi=103,valid=False)
    assert step(policy,out,i=1,hi=140)==out
    partial=advance({},dict(time=T,open=100,high=120,low=90,close=110),ev(T),policy,
                    entry=100,tp=102,sl=95,trailing={},entry_at=T+timedelta(seconds=20))
    assert not partial.get("outcome") and "entry_boundary_ambiguous_at" in partial


def test_before_tp_existing_trailing(policy):
    tr=dict(enabled=True,contract_version="shadow_hwm_trailing_v1",activation_profit_pct=.5,hwm_trail_pct=.2)
    out=step(policy,dict(high_water_mark=101),i=1,op=101,hi=101,lo=100,close=100,trailing=tr)
    assert out["outcome"]=="TRAILING_STOP"


def test_trade_identity_and_invalid_samples():
    t=dict(id=123,currency_pair="UNI_USDT",side="buy",amount="4",create_time=T.timestamp())
    row=normalize_trade(t,T)
    assert row["trade_id"]=="123" and row["amount"]=="4"
    for change in ({"id":None},{"amount":"nan"},{"amount":"-1"},{"side":"unknown"},{"create_time":T.timestamp()+1}):
        assert normalize_trade({**t,**change},T) is None


def test_evidence_missing_is_not_bearish(policy):
    assert build_evidence([],[],[],policy,T,T,T)["quality"]=="UNAVAILABLE"


def test_current_candle_cannot_authorize_its_own_tp(policy):
    s=step(policy)
    result=step(policy,s,i=1,hi=110,close=109)
    assert result["outcome"]=="TP_HIT"  # confirmation only became known at this close


def test_cvd_and_price_evidence_are_aligned(policy):
    candles=[dict(time=T+timedelta(minutes=i),open=100+i,high=102+i,low=99+i,close=101+i) for i in range(3)]
    buckets=[dict(time=T+timedelta(minutes=i),first_at=T+timedelta(minutes=i,seconds=1),last_at=T+timedelta(minutes=i,seconds=59),
                  max_gap=10,buy=8,sell=2,entry_delta=6) for i in range(3)]
    end=T+timedelta(minutes=3)
    e=build_evidence(buckets,candles,candles,policy,T,end,end)
    assert e["quality"]=="VALID" and e["cvd"]==18
    assert e["taker_ratio"]==.8 and e["delta_normalized"]==.6 and e["cvd_slope_normalized"]==.6
    assert build_evidence(buckets,candles,candles,policy,T,end,end+timedelta(seconds=61))["quality"]=="INCOMPLETE_OR_STALE"


def test_complete_apply_requires_no_empirical_approval_gate(policy):
    import asyncio
    from app.services.config_service import ConfigService
    class DB:
        async def execute(self,*args):raise AssertionError("No approval lookup on activation")
    asyncio.run(ConfigService().validate_shadow_l3_policy(DB(),{**policy.model_dump(),"mode":"APPLY"},"user"))


def test_compacted_evidence_replay_equals_incremental(policy):
    import json
    from scripts.shadow_l3_replay import replay
    candles=[];buckets=[];records=[];state={}
    for i in range(12):
        at=T+timedelta(minutes=i);end=at+timedelta(minutes=1)
        candles.append(dict(time=at,ingested_at=end,open=100+i,high=102+i,low=99+i,close=101+i))
        buckets.append(dict(time=at,first_at=at+timedelta(seconds=1),last_at=at+timedelta(seconds=59),
                            max_gap=10,buy=8,sell=2,entry_delta=6))
        e=build_evidence(buckets,candles,candles,policy,T,end,end)
        state=advance(state,candles[-1],e,policy,entry=100,tp=200,sl=90,trailing={},entry_at=T,timeout_candles=60)
        envelope={**e,"candle":candles[-1],"price_history":candles[-3:],"structure_history":candles[-3:],
                  "flow_buckets":buckets[-3:],"structure_timeframe":"1m","replay_lookback_seconds":180,
                  "flow_context":{"first_at":buckets[0]["first_at"],"cvd_before_window":sum(b["entry_delta"] for b in buckets[:-3]),"max_gap":10}}
        records.append({"evidence":envelope})
    data={"cutoff":end.isoformat(),"trades":[dict(id="synthetic",entry_timestamp=T.isoformat(),entry_price=100,
           tp_price=200,sl_price=90,trailing={},timeout_candles=60,fee_roundtrip_pct=None,baseline_gross_pct=None,decisions=records)]}
    encoded=json.loads(json.dumps(data,default=str))
    actual=replay(encoded,policy,T)["trades"][0]["state"]
    assert actual==state


def test_activation_keeps_initial_floor_when_current_flow_is_missing(policy):
    s=step(policy)
    s=step(policy,s,i=1)
    s=step(policy,s,i=2,hi=104,close=103,valid=False)
    assert s["continuation"] and s["floor_price"]==101.5
    s=step(policy,s,i=3,op=101,hi=103,lo=100,close=102,valid=False)
    assert s["outcome"]=="TRAILING_STOP" and s["exit_price"]==101
