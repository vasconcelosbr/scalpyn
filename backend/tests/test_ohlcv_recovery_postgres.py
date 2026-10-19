"""Real transactions in an isolated local schema; deterministic exchange responses."""
import asyncio
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4
import httpx
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.schemas.ohlcv_recovery import RecoveryManifest, OHLCVRecoveryConfig
from app.services import ohlcv_recovery as recovery
from app.services.ohlcv_collection_universe import collection_universe
from app.services.pool_service import get_active_pool_symbols

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
CONFIG = dict(enabled=True, max_manifest_candles=100, page_candles=3,
              max_pages_per_run=3, max_attempts_per_gap=2, request_spacing_seconds=1,
              retry_delay_seconds=2, http_timeout_seconds=5, statement_timeout_ms=5000,
              lock_timeout_ms=1000, dispatch_ttl_seconds=60)


def manifest(**changes):
    data = dict(exchange='gate.io', market_type='spot',
        symbol='BTCUSDT', timeframe='1m', start=START, end=START+timedelta(minutes=7),
        capture_contract_version='canonical-test', finalization_delay_seconds=0,
        reason='reviewed test scope')
    return RecoveryManifest.model_validate({**data, **changes})


@pytest_asyncio.fixture
async def database():
    url = os.environ.get('PR_A_TEST_DATABASE_URL')
    if not url:
        pytest.skip('PR_A_TEST_DATABASE_URL required for real PostgreSQL tests')
    parsed = make_url(url)
    assert parsed.host in {'127.0.0.1','localhost'} and parsed.database == 'scalpyn_pr_a_test'
    schema = 'recovery_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = uuid4()
    try:
        async with engine.begin() as conn:
            for statement in (
                'CREATE TABLE users(id uuid PRIMARY KEY)',
                'CREATE TABLE config_profiles(user_id uuid,pool_id uuid,config_type text,config_json jsonb)',
                '''CREATE TABLE ohlcv_capture_contracts(capture_contract_version text PRIMARY KEY,
                    finalization_delay_seconds integer,valid_from timestamptz, mode text,closed_table text,timeframes jsonb)''',
                '''CREATE TABLE ohlcv(time timestamptz,symbol text,exchange text,timeframe text,market_type text,
                    open numeric,high numeric,low numeric,close numeric,volume numeric,quote_volume numeric,
                    is_closed boolean,capture_contract_version text,ingested_at timestamptz,
                    PRIMARY KEY(time,symbol,exchange,timeframe))''',
                'CREATE TABLE pool_coins(symbol text,market_type text,is_active boolean,pool_id integer DEFAULT 1)',
                'CREATE TABLE pools(id integer,market_type text)',
                "INSERT INTO pools VALUES(1,'spot')",
                'CREATE TABLE pipeline_watchlist_assets(symbol text)',
                'CREATE TABLE market_metadata(symbol text)',
                'CREATE TABLE shadow_trades(id integer,symbol text,status text,source text,user_id uuid)',
            ):
                await conn.execute(text(statement))
            def migrate(connection):
                path = Path(__file__).parents[1]/'alembic/versions/246_ohlcv_recovery.py'
                spec = importlib.util.spec_from_file_location('recovery_migration', path)
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                with Operations.context(MigrationContext.configure(connection)):
                    module.upgrade()
            await conn.run_sync(migrate)
            # Deployment alone must not authorize or enable recovery.
            assert (await conn.execute(text('SELECT count(*) FROM ohlcv_recovery_owner'))).scalar() == 0
            await conn.execute(text('INSERT INTO users VALUES(:uid)'), {'uid': uid})
            await conn.execute(text("INSERT INTO ohlcv_recovery_owner(user_id,authorization_reason) VALUES(:uid,'test')"), {'uid': uid})
            await conn.execute(text("INSERT INTO config_profiles VALUES(:uid,NULL,'ohlcv_recovery',CAST(:cfg AS jsonb))"), {'uid':uid, 'cfg':json.dumps(CONFIG)})
            await conn.execute(text("INSERT INTO ohlcv_capture_contracts VALUES('canonical-test',0,:start,'CANONICAL','ohlcv','[\"1m\",\"5m\",\"30m\"]')"), {'start':START})
        yield factory, uid
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()


def exchange(*, omit=(), empty=False, status_code=200, open_candles=False, out_of_range=False):
    requests = []
    def handler(request):
        requests.append(dict(request.url.params))
        assert 'limit' not in request.url.params
        start, end = int(request.url.params['from']), int(request.url.params['to'])
        step = {'1m':60,'5m':300,'30m':1800}[request.url.params['interval']]
        rows = [] if empty else [[str(t), '10','2','3','1','2','5',not open_candles]
              for t in range(start, end+1, step) if t not in omit]
        if out_of_range:
            rows.append([str(end+60),'10','2','3','1','2','5',True])
        return httpx.Response(status_code, json=rows, headers={'Retry-After':'120.5'})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), requests


async def submit(factory, uid, scope=None):
    scope = scope or manifest()
    async with factory() as db:
        return UUID(await recovery.submit(db, uid, scope, scope.digest()))


async def unpace(factory):
    # Test clock control only. Production never bypasses cooldown.
    async with factory.begin() as db:
        await db.execute(text("UPDATE ohlcv_recovery_owner SET next_request_at='epoch'"))
        await db.execute(text("UPDATE ohlcv_recovery_jobs SET next_attempt_at='epoch'"))


@pytest.mark.asyncio
async def test_preview_readonly_submit_hash_authorization_and_bounds(database):
    factory, uid = database
    scope = manifest()
    async with factory() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        preview = await recovery.plan(db, uid, scope)
        assert preview['missing_candles'] == 7 and preview['writes'] is False
        assert preview['gaps'] == [{'start':START,'end':scope.end}]
        assert preview['source_recoverability'] == 'UNKNOWN_UNTIL_REQUESTED'
        assert (await db.execute(text('SELECT count(*) FROM ohlcv_recovery_jobs'))).scalar() == 0
    async with factory() as db:
        with pytest.raises(ValueError, match='hash'):
            await recovery.submit(db, uid, scope, 'unreviewed')
        with pytest.raises(PermissionError):
            await recovery.plan(db, uuid4(), scope)
    job = await submit(factory, uid)
    assert await submit(factory, uid) == job
    async with factory.begin() as db:
        await db.execute(text("UPDATE config_profiles SET config_json=jsonb_set(config_json,'{enabled}','false')"))
    async with factory() as db:
        assert (await recovery.run_page(db, job))['status'] == 'DISABLED'
        with pytest.raises(ValueError, match='disabled'):
            await recovery.submit(db, uid, scope, scope.digest())


@pytest.mark.asyncio
@pytest.mark.parametrize('timeframe,seconds', [('1m',60),('5m',300),('30m',1800)])
async def test_explicit_paging_restart_idempotence_and_arrival(database, timeframe, seconds):
    factory, uid = database
    job = await submit(factory, uid, manifest(timeframe=timeframe, end=START+timedelta(seconds=7*seconds)))
    client, requests = exchange()
    async with client:
        for expected in ('PARTIAL','PARTIAL','COMPLETE'):
            # New session/transaction on every page simulates worker restart.
            async with factory() as db:
                result = await recovery.run_page(db, job, client=client)
                assert result['status'] == expected
            await unpace(factory)
        async with factory() as db:
            before = (await db.execute(text('SELECT * FROM ohlcv ORDER BY time'))).all()
            assert len(before) == 7
            assert all(r.ingested_at > r.time + timedelta(days=1) for r in before)
            assert (await recovery.run_page(db, job, client=client))['status'] == 'COMPLETE'
            assert (await db.execute(text('SELECT * FROM ohlcv ORDER BY time'))).all() == before
            receipts = (await db.execute(text('SELECT receipt FROM ohlcv_recovery_attempts'))).scalars().all()
            assert sum(r['inserted'] for r in receipts) == 7
            assert all(r['response_sha256'] and r['decision_dispatches'] == 0 for r in receipts)
    assert [int(r['from']) for r in requests] == [int(START.timestamp())+s*seconds for s in (0,3,6)]
    assert int(requests[-1]['to']) == int(START.timestamp())+6*seconds


@pytest.mark.asyncio
async def test_partial_response_does_not_jump_gap_or_overwrite(database):
    factory, uid = database
    job = await submit(factory, uid)
    client, _ = exchange(omit=(int(START.timestamp())+60,))
    async with client, factory() as db:
        result = await recovery.run_page(db, job, client=client)
        assert result['next_time'] == START+timedelta(minutes=1)
        assert result['inserted'] == 2
        before = (await db.execute(text('SELECT * FROM ohlcv ORDER BY time'))).all()
    await unpace(factory)
    client, _ = exchange()
    async with client, factory() as db:
        await recovery.run_page(db, job, client=client)
        after = (await db.execute(text('SELECT * FROM ohlcv WHERE time IN (:a,:b) ORDER BY time'),
                                 {'a':START,'b':START+timedelta(minutes=2)})).all()
        assert before == after


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['empty','open','outside','rate'])
async def test_unrecoverable_and_rate_limited_are_explicit(database, kind):
    factory, uid = database
    job = await submit(factory, uid)
    client, requests = exchange(empty=kind=='empty', open_candles=kind=='open',
                                out_of_range=kind=='outside', status_code=429 if kind=='rate' else 200)
    async with client:
        async with factory() as db:
            first = await recovery.run_page(db, job, client=client)
            assert first['status'] == 'PARTIAL' and first['inserted'] == 0
            assert first['next_time'] == START
        async with factory() as db:
            wait = await recovery.run_page(db, job, client=client)
            assert wait['status'] == 'WAIT' and len(requests) == 1
            if kind == 'rate':
                assert first['error_code'] == 'GATE_RATE_LIMITED'
                assert wait['next_attempt_at'] > datetime.now(timezone.utc)+timedelta(seconds=115)
        await unpace(factory)
        async with factory() as db:
            assert (await recovery.run_page(db, job, client=client))['status'] == 'EXHAUSTED'
        async with factory() as db:
            assert (await recovery.run_page(db, job, client=client))['status'] == 'EXHAUSTED'
            assert (await recovery.status(db, uid, job))['missing_candles'] == 7
        assert len(requests) == 2


@pytest.mark.asyncio
async def test_lock_and_global_cooldown_across_jobs(database):
    factory, uid = database
    job = await submit(factory, uid)
    other = await submit(factory, uid, manifest().model_copy(update={'symbol':'ETH_USDT'}))
    client, requests = exchange()
    async with factory() as locked, client:
        await locked.execute(text('SELECT * FROM ohlcv_recovery_owner FOR UPDATE'))
        async with factory() as db:
            assert (await recovery.run_page(db, job, client=client))['status'] == 'BUSY'
        assert not requests
        await locked.rollback()
        async with factory() as db:
            await recovery.run_page(db, job, client=client)
        async with factory() as db:
            assert (await recovery.run_page(db, other, client=client))['status'] == 'WAIT'
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_atomic_rollback_on_checkpoint_failure(database):
    factory, uid = database
    job = await submit(factory, uid)
    async with factory.begin() as db:
        await db.execute(text("ALTER TABLE ohlcv_recovery_attempts ADD CONSTRAINT fail_receipt CHECK(false)"))
    client, _ = exchange()
    async with client, factory() as db:
        with pytest.raises(Exception, match='fail_receipt'):
            await recovery.run_page(db, job, client=client)
        await db.rollback()
        assert (await db.execute(text('SELECT count(*) FROM ohlcv'))).scalar() == 0
        row = (await db.execute(text('SELECT next_time,status FROM ohlcv_recovery_jobs'))).one()
        assert row.next_time == START and row.status == 'READY'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [dict(capture_contract_version='other'),
    dict(start=START-timedelta(minutes=1)), dict(end=START+timedelta(minutes=101)),
    dict(finalization_delay_seconds=1), dict(end=datetime(2099,1,1,tzinfo=timezone.utc))])
async def test_scope_fails_closed(database, change):
    factory, uid = database
    scope = RecoveryManifest.model_validate({**manifest().model_dump(), **change})
    async with factory() as db:
        with pytest.raises(ValueError):
            await recovery.plan(db, uid, scope)


@pytest.mark.asyncio
async def test_open_shadow_survives_pool_removal_restart_and_releases_last_dependency(database, monkeypatch):
    factory, _ = database
    from app.services import gate_ws_leader
    monkeypatch.setattr(gate_ws_leader, 'AsyncSessionLocal', factory)
    async with factory.begin() as db:
        await db.execute(text("INSERT INTO pool_coins(symbol,market_type,is_active,pool_id) VALUES('btcusdt','spot',true,1),('BTC_USDT','spot',true,1),('ETH_USDT','futures',true,2)"))
        await db.execute(text("INSERT INTO shadow_trades VALUES(1,'BTC_USDT','PENDING','L3',:a),(2,'btcusdt','RUNNING','LAB',:b),(3,'XLM_USDT','RUNNING','L3_REJECTED',:b),(4,'ETH_USDT','SL_HIT','L3',:a)"), {'a':uuid4(),'b':uuid4()})
    async with factory() as db:
        assert await collection_universe(db) == {'BTC_USDT':['active_pool','open_shadow'], 'XLM_USDT':['open_shadow']}
    assert await gate_ws_leader._resolve_spot_symbols() == ['BTC_USDT','XLM_USDT']
    async with factory.begin() as db:
        await db.execute(text("DELETE FROM pool_coins WHERE market_type='spot'"))
        await db.execute(text("UPDATE shadow_trades SET status='SL_HIT' WHERE id=1"))
    async with factory() as db:
        assert await get_active_pool_symbols(db,'spot') == []
        assert await collection_universe(db) == {'BTC_USDT':['open_shadow'],'XLM_USDT':['open_shadow']}
    assert await gate_ws_leader._resolve_spot_symbols() == ['BTC_USDT','XLM_USDT']
    async with factory.begin() as db:
        await db.execute(text("UPDATE shadow_trades SET status='TP_HIT' WHERE id=2"))
    async with factory() as db:
        assert await collection_universe(db) == {'XLM_USDT':['open_shadow']}
    assert await gate_ws_leader._resolve_spot_symbols() == ['XLM_USDT']


@pytest.mark.parametrize('change', [dict(exchange='binance'),dict(market_type='futures'),
    dict(timeframe='15m'), dict(start='2026-01-01T00:00:01Z'),dict(start='2026-01-01T00:00:00'),
    dict(symbol='BTC_USDT;DROP'),dict(end=START)])
def test_manifest_rejects_unsupported_or_unaligned_scopes(change):
    with pytest.raises(ValueError):
        RecoveryManifest.model_validate({**manifest().model_dump(), **change})


def test_config_has_no_implicit_enablement_or_budgets():
    with pytest.raises(ValueError):
        OHLCVRecoveryConfig.model_validate({})
    with pytest.raises(ValueError):
        OHLCVRecoveryConfig.model_validate({**CONFIG,'page_candles':1001})


@pytest.mark.asyncio
async def test_queued_manifest_rechecks_contract_before_any_http(database):
    factory, uid = database
    job = await submit(factory, uid)
    async with factory.begin() as db:
        await db.execute(text("UPDATE ohlcv_capture_contracts SET mode='SHADOW'"))
    client, requests = exchange()
    async with client, factory() as db:
        with pytest.raises(ValueError, match='active canonical'):
            await recovery.run_page(db, job, client=client)
    assert not requests


@pytest.mark.asyncio
async def test_task_obeys_page_budget_and_disable_between_deliveries(database, monkeypatch):
    factory, uid = database
    job = await submit(factory, uid)
    from app.tasks.ohlcv_recovery import recover_async
    import app.database as db_module
    monkeypatch.setattr(db_module, 'CeleryAsyncSessionLocal', factory)
    async with factory.begin() as db:
        await db.execute(text("UPDATE config_profiles SET config_json=jsonb_set(config_json,'{max_pages_per_run}','1')"))
    client, requests = exchange()
    original = recovery.run_page
    async def page(db, job_id):
        return await original(db, job_id, client=client)
    monkeypatch.setattr(recovery, 'run_page', page)
    async with client:
        result = await recover_async(job)
        assert result['next_time'] == START+timedelta(minutes=3) and len(requests) == 1
        async with factory.begin() as db:
            await db.execute(text("UPDATE config_profiles SET config_json=jsonb_set(config_json,'{enabled}','false')"))
        assert (await recover_async(job))['status'] == 'DISABLED'
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_config_service_and_http_enforce_owner_before_writes():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from fastapi import HTTPException
    from app.services.config_service import ConfigService
    from app.api.config import update_config, get_config
    result = Mock(); result.scalar.return_value = False
    db = SimpleNamespace(execute=AsyncMock(return_value=result), add=Mock(), commit=AsyncMock())
    with pytest.raises(PermissionError):
        await ConfigService().update_config(db,'ohlcv_recovery',uuid4(),CONFIG,uuid4())
    with pytest.raises(HTTPException) as error:
        await update_config('ohlcv_recovery',CONFIG,pool_id=None,db=db,user_id=uuid4())
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await get_config('ohlcv_recovery',pool_id=None,db=db,user_id=uuid4())
    assert error.value.status_code == 403
    db.add.assert_not_called()
    db.commit.assert_not_called()
