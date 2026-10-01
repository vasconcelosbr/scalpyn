import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import pump_monitor_engine as eng
from app.services import pump_research as research
from app.services import pump_universe as universe


@pytest.mark.parametrize("cap,accepted", [
    (999999999, False), (1000000000, True), (1000000001, True),
    (None, False), (float('nan'), False), (float('inf'), False),
    (-float('inf'), False), (-1, False), (True, False), ('invalid', False),
])
def test_billion_boundary_and_invalid_caps(cap, accepted):
    symbols, _ = universe.eligible_symbols(['A'], {'A': cap}, 1000000000)
    assert symbols == (['A'] if accepted else [])


def test_empty_cap_map_fails_closed_and_unknown_is_excluded():
    assert universe.eligible_symbols(['A'], {}, 1000000000)[0] == []
    assert universe.eligible_symbols(['A', 'B'], {'A': 1000000000}, 1000000000)[0] == ['A']


@pytest.mark.parametrize('minimum', [None, -1, float('nan'), float('inf'), False, True, 10**1000])
def test_invalid_threshold_rejected(minimum):
    config = deepcopy(eng.DEFAULT_CONFIG)
    config['universe_filter']['min_market_cap_usd'] = minimum
    with pytest.raises(ValueError, match='min_market_cap_usd'):
        eng.validate_config(config)


def test_default_reuses_operator_approved_pool_threshold():
    from app.services.pool_selection import extract_profile_discovery_thresholds
    _, cap, _ = extract_profile_discovery_thresholds({'filters': {'conditions': [
        {'field': 'market_cap', 'operator': '>=', 'value': 1000000000, 'required': False}]}})
    assert eng.effective_config({})['universe_filter']['min_market_cap_usd'] == cap


def test_support_endpoint_completes_label_without_becoming_observation():
    minute = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
    config = eng.effective_config({})
    series = {minute + k * 60000: {'bucket_high': 100.1, 'bucket_low': 99.9,
                                  'bucket_close': 100, 'bucket_partial': False} for k in range(60)}
    missing = research.compute_labels(series, t_ms=minute, cost_pct=.2,
                                      labels=config['research']['labels'])
    assert missing['returns']['60']['reason'] == 'price_gap_endpoint'
    rec, vk, ck = research.price_support_row('REMOVED', minute_ms=minute + 60 * 60000,
        bucket={'high_price': 100.1, 'low_price': 99.9, 'close_price': 100, 'partial': False},
        cycle_at_ms=minute + 61 * 60000, config_meta=config['_meta'])
    assert rec['categorical']['_research_role'] == 'label_drain'
    assert rec['score'] is None and rec['alerts'] == [] and rec['pool_member'] is False
    assert rec['slippage_buy_pct'] is None and vk == () and ck == ()
    series[minute + 60 * 60000] = rec
    complete = research.compute_labels(series, t_ms=minute, cost_pct=.2,
                                       labels=config['research']['labels'])
    assert complete['returns']['60']['reason'] is None
    assert complete['returns']['60']['net'] == pytest.approx(-.2)
    now = datetime.fromtimestamp((minute + 65 * 60000) / 1000, tz=timezone.utc)
    assert research.label_cutoff(now, config['research']['labels']) == now - timedelta(minutes=65)


def test_drain_query_is_bounded_and_support_cannot_extend_frontier(monkeypatch):
    captured = {}
    class Result:
        def scalars(self): return self
        def all(self): return ['REMOVED']
    class DB:
        async def execute(self, sql, params):
            captured.update(sql=str(sql), params=params)
            return Result()
    async def caps(db, symbols): return {'A': 1000000000, 'B': 1}
    monkeypatch.setattr(universe, 'load_market_cap_map', caps)
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    eligible, drains, _ = asyncio.run(universe.select_universe(DB(), ['A', 'B'], eng.effective_config({}), now))
    assert eligible == ['A'] and drains == ['REMOVED']
    assert captured['params']['lo'] == now - timedelta(minutes=60)
    assert captured['params']['eligible'] == ['A']
    assert "m.categorical->>'_research_role'" in captured['sql']
    assert 'NOT EXISTS' in captured['sql'] and 'max(m.ts)' in captured['sql']
    assert '>= :minute' in captured['sql']  # final endpoint inclusive, next minute stops


@pytest.mark.parametrize('eligible,drains,cadence', [
    (['BIG'], ['SMALL'], 1), ([], ['SMALL'], 1), ([], [], 1),
    (['BIG'], ['SMALL'], 2), ([], ['SMALL'], 2)])
@pytest.mark.parametrize('fail_first_support', [False, True])
@pytest.mark.parametrize('capture_failure', [False, True])
def test_cycle_separates_ranking_and_drain_and_clears_empty_envelope(monkeypatch, eligible, drains, cadence, fail_first_support, capture_failure):
    from app.services import pump_monitor_service as svc
    from app.services import pump_opportunity_service as opportunities
    from app import database
    captured = {'collected': [], 'built': [], 'records': [], 'synced': None, 'latest': None}
    redis_values = {}
    collection_attempts = {}
    config = deepcopy(eng.effective_config({'universe_pool_id': '00000000-0000-0000-0000-000000000001'}))
    config['research']['every_n_minutes'] = cadence
    now_ms = int(datetime(2026, 10, 1, 12, 2, 10, tzinfo=timezone.utc).timestamp()*1000)
    minute = ((now_ms-3000)//60000)*60000-60000
    bucket = {'bucket_start_ms': minute, 'high_price': 100, 'low_price': 100,
              'close_price': 100, 'partial': False}
    class DB:
        async def execute(self, *args, **kwargs): return None
    async def run(fn, celery=False):
        if fn.__name__ == '_load':
            return ({s: {minute: bucket} for s in eligible}, {}, {}, {})
        if '_universe' in fn.__code__.co_names: return ['BIG', 'SMALL']
        if 'select_universe' in fn.__code__.co_names:
            return eligible, drains, {'BIG': 1000000000}
        return await fn(DB())
    async def collect(symbol, minutes, cfg):
        captured['collected'].append(symbol)
        collection_attempts[symbol] = collection_attempts.get(symbol, 0) + 1
        if fail_first_support and symbol in drains and collection_attempts[symbol] == 1:
            raise RuntimeError('transient support collection failure')
        return {'buckets': [bucket], 'book': None}
    def build(symbol, **kwargs):
        captured['built'].append(symbol)
        return {'symbol': symbol, 'indicators': {}, 'pump_monitor_score': 50,
                'score_components': {}, 'alerts_active': []}
    async def sync(user, rows, cfg, now, state):
        captured['synced'] = [r['symbol'] for r in rows]
        return []
    async def write(run, records, keys):
        captured['records'] = records
        return True
    class Redis:
        async def get(self, key): return redis_values.get(key)
        async def set(self, key, value, ex):
            redis_values[key] = value
            if ':latest:' in key: captured['latest'] = json.loads(value)
    async def redis(): return Redis()
    monkeypatch.setattr(database, 'run_db_task', run)
    monkeypatch.setattr(svc, '_now_ms', lambda: now_ms)
    monkeypatch.setattr(svc, '_collect_symbol', collect)
    monkeypatch.setattr(eng, 'build_row', build)
    monkeypatch.setattr(svc, '_sync_realtime_pools', sync)
    monkeypatch.setattr(svc, '_redis', redis)
    monkeypatch.setattr(research, 'write_safely', write)
    stages=[]
    async def capture(*args):
        stages.append('capture')
        if capture_failure:raise TimeoutError('fixture capture failure')
    async def labels(*args):stages.append('labels')
    monkeypatch.setattr(opportunities, 'ingest', capture)
    monkeypatch.setattr(opportunities, 'label_batch', labels)
    asyncio.run(svc._cycle_for_user('USER', config))
    assert stages==['capture','labels']
    if fail_first_support and drains:
        state = json.loads(redis_values['pump_monitor:state:USER'])
        assert 'research_support_last_minute' not in state
        captured['built'] = []
        captured['collected'] = []
        asyncio.run(svc._cycle_for_user('USER', config))
        assert stages==['capture','labels','capture','labels']
        assert json.loads(redis_values['pump_monitor:state:USER'])['research_support_last_minute'] == minute
    assert captured['built'] == eligible and captured['synced'] == eligible
    assert sorted(captured['collected']) == sorted(set(eligible) | set(drains))
    assert [r['symbol'] for r in captured['latest']['rows']] == eligible
    support = [r for r in captured['records'] if (r.get('categorical') or {}).get('_research_role') == 'label_drain']
    assert [r['symbol'] for r in support] == drains
    assert all(r['score'] is None and not r['pool_member'] for r in support)
    observations = [r for r in captured['records'] if r not in support]
    assert len(observations) == (len(eligible) if cadence == 1 and not (fail_first_support and drains) else 0)


def test_label_targets_and_export_exclude_price_only_support():
    root = Path(__file__).resolve().parents[1]
    assert "COALESCE(m.categorical->>'_research_role', '') <> 'label_drain'" in (root/'app/services/pump_research.py').read_text()
    assert "COALESCE(m.categorical->>'_research_role', '') <> 'label_drain'" in (root/'scripts/export_pump_research.py').read_text()


def test_drain_endpoint_inclusive_then_cleanup_and_labelled_cleanup(monkeypatch):
    """Execute the real selection SQL with only PostgreSQL dialect adaptation."""
    import sqlite3
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE pump_research_minute(symbol TEXT, ts TEXT, categorical TEXT)')
    connection.execute('CREATE TABLE pump_research_labels(symbol TEXT, ts TEXT, label_set_version TEXT)')
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    observation = (now - timedelta(minutes=60)).isoformat()
    connection.execute('INSERT INTO pump_research_minute VALUES(?,?,?)', ('REMOVED', observation, '{}'))
    connection.execute('INSERT INTO pump_research_minute VALUES(?,?,?)',
                       ('REMOVED', (now-timedelta(minutes=1)).isoformat(), '{"_research_role":"label_drain"}'))
    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return [r[0] for r in self.rows]
    class DB:
        async def execute(self, query, params):
            sql = str(query).replace('m.symbol = ANY(CAST(:eligible AS text[]))',
                                     'm.symbol IN (SELECT value FROM json_each(:eligible))')
            sql = sql.replace('max(m.ts) + make_interval(mins => :h) >= :minute',
                              'julianday(max(m.ts)) + :h / 1440.0 >= julianday(:minute)')
            values = {k: v.isoformat() if isinstance(v, datetime) else json.dumps(v) if isinstance(v, list) else v
                      for k, v in params.items()}
            return Result(connection.execute(sql, values).fetchall())
    async def caps(db, symbols): return {'BIG': 1000000000}
    monkeypatch.setattr(universe, 'load_market_cap_map', caps)
    config = eng.effective_config({})
    assert asyncio.run(universe.select_universe(DB(), ['BIG'], config, now))[1] == ['REMOVED']
    assert asyncio.run(universe.select_universe(DB(), ['BIG'], config, now+timedelta(minutes=1)))[1] == []
    connection.execute('INSERT INTO pump_research_labels VALUES(?,?,?)',
                       ('REMOVED', observation, config['research']['labels']['version']))
    assert asyncio.run(universe.select_universe(DB(), ['BIG'], config, now))[1] == []
    connection.close()
