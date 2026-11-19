from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from app.schemas.mtf_chart import MTFChartConfig, MTFPumpFlowConfig, experimental_preset, pump_flow_preset
from app.services.mtf_pump_flow import build_context, summarize, load_context, comparison_report, CAPITAL_SQL, ASSET_SQL, REPORT_SQL
from app.services.mtf_chart_engine import evaluate
from test_mtf_chart_engine import setup_frames

CUT = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
KNOWN = CUT+timedelta(seconds=30)


def config(**overrides):
    return MTFPumpFlowConfig.model_validate({**pump_flow_preset(), 'enabled': True, **overrides})


def minutes(n=240, buy=6, sell=4):
    return [{'minute': CUT-timedelta(minutes=i), 'buy_usdt': buy, 'sell_usdt': sell,
             'symbols': 2, 'complete_symbols': 2, 'partial': False, 'computed_at': CUT}
            for i in range(n, 0, -1)]


def build(capitals=None, assets=None, **kwargs):
    return build_context(minutes() if capitals is None else capitals, minutes(5) if assets is None else assets,
                         symbol='TEST_USDT', monitored_symbols={'TEST_USDT'}, cutoff=CUT, known_at=KNOWN,
                         cfg=config(), **kwargs)


def test_three_windows_independent_aggregates_and_confirmed_asset():
    result=build()
    assert result['confirmation']=='confirmed'
    for tf,n in [('4h',240),('1h',60),('5m',5)]:
        r=result['capital'][tf]
        assert r['net_usdt']==n*2 and r['ratio']==.2 and r['coverage']==1
        assert r['window_end']==CUT.isoformat() and r['state']=='inflow'
    assert result['asset']['buy_persistence']==1
    assert result['operational_effect'] is False


def test_capital_requirement_only_changes_shadow_confirmation():
    capital=minutes(buy=4,sell=6)
    result=build(capital)
    assert result['asset']['state']=='confirmed'
    assert result['confirmation']=='not_confirmed'
    result=build_context(capital,minutes(5),symbol='TEST_USDT',monitored_symbols={'TEST_USDT'},
                         cutoff=CUT,known_at=KNOWN,cfg=config(require_capital={'4h':False,'1h':False,'5m':False}))
    assert result['confirmation']=='confirmed'


def test_missing_data_never_becomes_neutral_or_confirmed():
    for rows in [[], minutes(5,buy=0,sell=0), [{**r,'partial':True} for r in minutes(5)], minutes(5)[:-1]]:
        assert build(assets=rows)['confirmation']=='unavailable'
    assert build(capitals=[])['confirmation']=='unavailable'
    r=build_context(minutes(),minutes(5),symbol='OUTSIDE_USDT',monitored_symbols={'TEST_USDT'},
                    cutoff=CUT,known_at=KNOWN,cfg=config())
    assert r['asset']['reason']=='asset_not_in_pump_universe'


def test_future_and_late_data_are_not_visible_and_duplicates_rejected():
    rows=minutes(5)
    extra={**rows[-1],'minute':CUT,'buy_usdt':999999}
    assert summarize(rows+[extra],5,CUT,KNOWN,.8)['net_usdt']==10
    rows[-1]['computed_at']=KNOWN+timedelta(seconds=1)
    assert summarize(rows,5,CUT,KNOWN,.8)['state']=='unavailable'
    assert summarize(minutes(5)+minutes(5)[-1:],5,CUT,KNOWN,.8)['reason']=='duplicate_minute'


def test_coverage_uses_full_denominator_and_symbol_completeness():
    r=summarize(minutes(3),5,CUT,KNOWN,.8)
    assert r['coverage']==.6 and r['state']=='unavailable'
    r=summarize([{**r,'complete_symbols':1} for r in minutes()],240,CUT,KNOWN,.8,capital=True)
    assert r['coverage']==.5 and r['state']=='unavailable'


def test_flow_config_never_changes_score_veto_status_plan_or_engine_identity():
    base=MTFChartConfig.model_validate(experimental_preset())
    flow=base.model_copy(update={'pump_flow':config(asset_min_delta=.9)})
    assert base.engine_dump()==flow.engine_dump()
    frames=setup_frames()
    assert evaluate('BTC_USDT',deepcopy(frames),base,28800,.01,'UP')==evaluate('BTC_USDT',deepcopy(frames),flow,28800,.01,'UP')


def test_stale_or_disabled_context_and_threshold_validation():
    r=build_context(minutes(),minutes(5),symbol='TEST_USDT',monitored_symbols={'TEST_USDT'},cutoff=CUT,
                    known_at=CUT+timedelta(seconds=601),cfg=config())
    assert r['confirmation']=='unavailable'
    with pytest.raises(ValueError):config(capital_neutral_ratio={'4h':.03})
    with pytest.raises(ValueError):config(asset_min_delta=0)
    with pytest.raises(ValueError):config(capital_neutral_ratio={'4h':2,'1h':.03,'5m':.03})


@pytest.mark.asyncio
async def test_disabled_does_not_read_sources():
    db=AsyncMock()
    assert (await load_context(db,'uid','TEST_USDT',CUT,None))['confirmation']=='disabled'
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_source_failure_is_isolated_and_returns_unavailable():
    with patch('app.services.pump_monitor_service._redis',AsyncMock(side_effect=RuntimeError('offline'))):
        r=await load_context(AsyncMock(),'uid','TEST_USDT',CUT,config())
    assert r['confirmation']=='unavailable' and r['reasons']==['source_unavailable']
    assert r['operational_effect'] is False


def test_sql_ownership_and_time_boundaries():
    assert 'user_id=:uid' in CAPITAL_SQL and 'computed_at<=:known_at' in CAPITAL_SQL
    assert 'symbol=:symbol' in ASSET_SQL and 'computed_at<=:known_at' in ASSET_SQL
    assert 'e.user_id=:uid AND e.watchlist_id=:wid' in REPORT_SQL
    assert 'DISTINCT ON (e.id)' in REPORT_SQL
    assert "e.payload->'pump_flow'->'config'->>'enabled'='true'" in REPORT_SQL
    for sql in [CAPITAL_SQL,ASSET_SQL,REPORT_SQL]:
        assert not any(word in sql.upper().split() for word in ['INSERT','UPDATE','DELETE'])
