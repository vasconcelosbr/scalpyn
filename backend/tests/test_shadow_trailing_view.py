from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest
from app.services.shadow_trailing_view import project, asset_regime, regime_label, attach_trailing_views

NOW = datetime(2026,9,6,12,tzinfo=timezone.utc)


def trade(**kwargs):
    base = dict(id='trade',user_id='owner',symbol='UNI_USDT',exchange='gate',source='L3',direction='LONG',
        entry_price=100,entry_timestamp=NOW-timedelta(hours=1),exit_price=None,exit_timestamp=None,
        sl_price=98,tp_price=101.5,status='RUNNING',
        config_snapshot={'shadow_l3_exit_policy':{'config':{'mode':'APPLY','step_trigger_pct':.5,'step_floor_pct':.5,'initial_buffer_pct':.5}}},
        l3_exit={'state':'PRE_TP','quality':'VALID','last_evaluated_at':(NOW-timedelta(minutes=1)).isoformat()})
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_waiting_distance_and_no_fabricated_activation():
    v=project(trade(),quote={'close':101,'at':NOW},now=NOW)
    assert v.state=='WAITING'
    assert v.remaining_pp==pytest.approx(.5)
    assert v.activation.price is None
    assert v.pending_reason=='WAITING_TRIGGER'


def test_reached_target_requires_evaluation_and_quality():
    t=trade();t.l3_exit['quality']='UNAVAILABLE'
    v=project(t,quote={'close':102,'at':NOW},now=NOW)
    assert v.remaining_pp==0 and v.pending_reason=='DATA_UNAVAILABLE'
    t.l3_exit['quality']='VALID'
    assert project(t,quote={'close':102,'at':NOW},now=NOW).pending_reason=='WAITING_FLOW_PRICE_CONFIRMATION'


def test_quote_before_entry_cannot_claim_post_entry_profit():
    v=project(trade(entry_timestamp=NOW),quote={'close':110,'at':NOW-timedelta(minutes=1)},now=NOW)
    assert v.observed.price is None and v.remaining_pp is None


def test_effective_floor_schedule_next_step_and_pending_floor():
    t=trade();t.l3_exit.update(continuation=True,state='TIGHTENED',floor_price=104,high_water_mark=104.2,
        floor_schedule=[{'price':102.5,'available_at':(NOW-timedelta(minutes=1)).isoformat()},
                        {'price':104,'available_at':(NOW+timedelta(minutes=1)).isoformat()}])
    v=project(t,quote={'close':104.1,'at':NOW},now=NOW,activation={'candle_at':NOW-timedelta(minutes=10)})
    assert v.state=='TIGHTENED' and v.floor.price==102.5 and v.pending_floor.price==104
    assert v.floor.pct==pytest.approx(2.5) and v.next_step.pct==pytest.approx(4.5)
    assert v.activation.price==101.5 and v.origin=='POST_TP'
    t.l3_exit['pending_exit']={'available_at':NOW.isoformat()}
    assert project(t,now=NOW).state=='EXIT_PENDING'


def test_observation_never_presents_candidate_as_applied():
    t=trade();t.config_snapshot['shadow_l3_exit_policy']['config']['mode']='OBSERVE'
    t.l3_exit.update(continuation=True,floor_price=103,state='CONTINUATION')
    v=project(t,now=NOW)
    assert v.state=='OBSERVATION' and v.floor.price is None


def test_legacy_floor_not_invented_and_disabled_distinct():
    t=trade(config_snapshot={'trailing':{'enabled':True,'activation_profit_pct':1}},l3_exit=None)
    assert project(t,now=NOW).state=='UNAVAILABLE'
    t.config_snapshot={}
    assert project(t,now=NOW).state=='DISABLED'


def test_pre_tp_trailing_and_closed_execution_not_later_quote():
    t=trade();t.l3_exit.update(floor_price=100.5)
    assert project(t,now=NOW).origin=='PRE_TP'
    t.status='COMPLETED';t.exit_price=100.2;t.exit_timestamp=NOW-timedelta(minutes=2)
    v=project(t,now=NOW,quote={'close':110,'at':NOW})
    assert v.state=='CLOSED' and v.observed.price==100.2


def test_regimes_do_not_treat_missing_volatility_or_breakout_as_neutral():
    assert regime_label('TRENDING_BULL')=='Bullish'
    assert regime_label('SIDEWAYS')=='Neutral'
    assert regime_label('HIGH_VOLATILITY') is None and regime_label('BREAKOUT') is None
    assert asset_regime({},trade(),NOW).label is None
    raw={k:{'value':v,'status':'VALID','timeframe':'5m','candle_closed':True,'market_type':'spot','source_provider':'gate.io',
        'source_timestamp':NOW.isoformat(),'available_at':NOW.isoformat(),'config_user_id':'owner'}
        for k,v in {'adx':40,'ema20':103,'ema50':102,'ema200':101}.items()}
    assert asset_regime(raw,trade(),NOW).label=='Bullish'
    assert asset_regime(raw,trade(user_id='other'),NOW).label is None
    assert asset_regime(raw,trade(),NOW+timedelta(hours=1)).quality=='STALE'
    raw['adx']['available_at']=(NOW+timedelta(minutes=1)).isoformat()
    assert asset_regime(raw,trade(),NOW).label is None


@pytest.mark.asyncio
async def test_other_users_are_excluded_before_query():
    class DB:
        async def execute(self,*args,**kwargs):
            raise AssertionError('No query for unauthorized rows')
    await attach_trailing_views(DB(),[trade()], 'other')
    await attach_trailing_views(DB(),[trade(source='L1'),trade(direction='SHORT')], 'owner')
