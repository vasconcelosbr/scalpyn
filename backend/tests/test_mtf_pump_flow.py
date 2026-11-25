from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_unavailable_reason_separates_low_coverage_from_delayed_trailing_minute():
    low = summarize(minutes(3), 5, CUT, KNOWN, .8)
    assert low['reason'] == 'incomplete_window' and low['trailing_minute_ready'] is True
    late = summarize(minutes(5)[:-1], 5, CUT, KNOWN, .8)
    assert late['coverage'] == .8 and late['reason'] == 'delayed_window' and late['trailing_minute_ready'] is False
    quiet = summarize(minutes(5, buy=0, sell=0), 5, CUT, KNOWN, .8)
    assert quiet['reason'] == 'no_trades'
    # Incident 25/11 18:30Z: complete_symbols 1, 2, 24, 24, 24 of 24 -> 75/120.
    rows = [{**r, 'symbols': 24, 'complete_symbols': c} for r, c in zip(minutes(5), (1, 2, 24, 24, 24))]
    incident = summarize(rows, 5, CUT, KNOWN, .8, capital=True)
    assert incident['coverage'] == .625 and incident['reason'] == 'incomplete_window'


# ── MTF_PUMP_FLOW_REVISION ───────────────────────────────────────────────────
from app.services import mtf_pump_flow as flow_module
from app.services.mtf_pump_flow import revisable, revise, revise_due, REVISION_CANDIDATES_SQL, REVISION_INSERT_SQL


def test_revision_schedule_never_changes_flow_or_engine_identity():
    with_rev, without = config(), config(revision=None)
    assert with_rev.revision is not None and without.revision is None
    assert with_rev.identity_dump() == without.identity_dump() and 'revision' not in with_rev.identity_dump()
    a = build_context(minutes(), minutes(5), symbol='TEST_USDT', monitored_symbols={'TEST_USDT'}, cutoff=CUT, known_at=KNOWN, cfg=with_rev)
    b = build_context(minutes(), minutes(5), symbol='TEST_USDT', monitored_symbols={'TEST_USDT'}, cutoff=CUT, known_at=KNOWN, cfg=without)
    assert a['config_hash'] == b['config_hash'] and 'revision' not in a['config']


def test_revision_must_fit_under_freshness_limit():
    with pytest.raises(ValueError):
        config(revision={'enabled': True, 'delay_seconds': 600, 'batch_limit': 10})
    with pytest.raises(ValueError):
        config(revision={'enabled': True, 'delay_seconds': 120})  # batch_limit required: fail-closed


def _delayed_original():
    # Captured 30 s after the close: the last closed minute was not published yet.
    return build_context(minutes(), minutes(5)[:-1], symbol='TEST_USDT', monitored_symbols={'TEST_USDT'},
                         cutoff=CUT, known_at=KNOWN, cfg=config())


def test_revisable_only_when_flow_was_read_at_capture():
    original = _delayed_original()
    assert original['confirmation'] == 'unavailable' and original['asset']['reason'] == 'delayed_window'
    assert revisable(original)
    outside = build_context(minutes(), minutes(5), symbol='OUT_USDT', monitored_symbols={'TEST_USDT'},
                            cutoff=CUT, known_at=KNOWN, cfg=config())
    assert not revisable(outside)  # membership is never re-derived later
    late = build_context(minutes(), minutes(5), symbol='TEST_USDT', monitored_symbols={'TEST_USDT'},
                         cutoff=CUT, known_at=CUT+timedelta(seconds=601), cfg=config())
    assert not revisable(late)
    assert not revisable(build())  # already confirmed


def test_revise_reads_with_original_thresholds_and_records_provenance():
    original = _delayed_original()
    revised = revise(minutes(), minutes(5), flow=original, symbol='TEST_USDT', known_at=CUT+timedelta(seconds=120))
    assert revised['confirmation'] == 'confirmed' and revised['asset']['reason'] is None
    assert revised['config_hash'] == original['config_hash']
    assert revised['revision_of']['original_confirmation'] == 'unavailable'
    assert revised['revision_of']['original_asset_reason'] == 'delayed_window'
    assert revised['revision_of']['delay_seconds'] == 120
    assert revised['operational_effect'] is False


class _Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def all(self): return self.rows


@pytest.mark.asyncio
async def test_revise_due_writes_once_per_due_evaluation_and_skips_others():
    original = _delayed_original()
    due = {'id': 'e1', 'symbol': 'TEST_USDT', 'flow': original}
    early = {'id': 'e2', 'symbol': 'TEST_USDT', 'flow': {**original, 'cutoff': (CUT+timedelta(minutes=5)).isoformat()}}
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=AsyncMock())
    db.execute.return_value = _Rows([due, early])
    db.scalar.return_value = 'e1'
    now = CUT+timedelta(seconds=150)
    with patch.object(flow_module, 'read_rows', AsyncMock(return_value=(minutes(), minutes(5)))) as reader:
        written = await revise_due(db, 'uid', config(), now, clock=lambda: now)
    assert written == 1 and reader.await_count == 1
    params = db.execute.await_args.args[1]
    assert params['due'] == now-timedelta(seconds=120) and params['since'] == now-timedelta(seconds=600)
    insert = db.scalar.await_args.args[1]
    assert insert['eid'] == 'e1' and insert['confirmation'] == 'confirmed' and insert['delay_seconds'] == 150
    assert insert['original_confirmation'] == 'unavailable'


@pytest.mark.asyncio
async def test_revise_due_disabled_reads_nothing():
    db = AsyncMock()
    assert await revise_due(db, 'uid', config(revision=None), CUT) == 0
    assert await revise_due(db, 'uid', config(revision={'enabled': False, 'delay_seconds': 120, 'batch_limit': 1}), CUT) == 0
    db.execute.assert_not_called()


def test_revision_sql_is_owned_append_only_and_reported_as_subset():
    assert 'e.user_id=:uid' in REVISION_CANDIDATES_SQL and 'NOT EXISTS' in REVISION_CANDIDATES_SQL
    assert 'ON CONFLICT (evaluation_id) DO NOTHING' in REVISION_INSERT_SQL
    assert 'LEFT JOIN mtf_pump_flow_revisions r ON r.evaluation_id=e.id' in REPORT_SQL
    assert "'revised_'||revised" in REPORT_SQL


def test_migration_revision_block_matches_preset():
    import importlib.util
    path = Path(__file__).resolve().parents[1]/'alembic'/'versions'/'253_mtf_pump_flow_revisions.py'
    spec = importlib.util.spec_from_file_location('m253', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.REVISION == pump_flow_preset()['revision']
    assert module.down_revision == '252_mtf_signal_replay'


@pytest.mark.asyncio
async def test_revise_due_one_bad_evaluation_never_blocks_the_batch():
    original = _delayed_original()
    bad = {'id': 'bad', 'symbol': 'TEST_USDT', 'flow': {**original, 'config': {**original['config'], 'min_coverage': 7}}}
    good = {'id': 'e1', 'symbol': 'TEST_USDT', 'flow': original}
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=AsyncMock())
    db.execute.return_value = _Rows([bad, good])
    db.scalar.return_value = 'e1'
    now = CUT+timedelta(seconds=150)
    with patch.object(flow_module, 'read_rows', AsyncMock(return_value=(minutes(), minutes(5)))):
        assert await revise_due(db, 'uid', config(), now, clock=lambda: now) == 1
    assert db.scalar.await_args.args[1]['eid'] == 'e1'
