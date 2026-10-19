"""Real PostgreSQL contract tests. Only an explicitly named local test DB is allowed."""
import importlib.util
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.services import ohlcv_comparison as comparison
from app.schemas.ohlcv_comparison import OHLCVComparisonConfig
from test_ohlcv_queue_isolation import CONFIG

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def database():
    url = os.environ.get('PR_A_TEST_DATABASE_URL')
    if not url:
        pytest.skip('PR_A_TEST_DATABASE_URL is required for real PostgreSQL tests')
    parsed = make_url(url)
    assert parsed.host in {'127.0.0.1', 'localhost'} and parsed.database == 'scalpyn_pr_a_test'
    schema = 'pr_a_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            for statement in (
                'CREATE TABLE users(id uuid PRIMARY KEY)',
                'CREATE TABLE config_profiles(user_id uuid, pool_id uuid, config_type text, config_json jsonb)',
                'CREATE TABLE ohlcv_capture_contracts(capture_contract_version text PRIMARY KEY, valid_from timestamptz, mode text, canonical_read_enabled boolean)',
                '''CREATE TABLE ohlcv(time timestamptz,symbol text,exchange text,timeframe text,market_type text,
                    open numeric, high numeric, low numeric, close numeric, volume numeric, quote_volume numeric,
                    PRIMARY KEY(time,symbol,exchange,timeframe,market_type))''',
                'CREATE TABLE ohlcv_shadow (LIKE ohlcv INCLUDING ALL)',
                'ALTER TABLE ohlcv_shadow ADD COLUMN capture_contract_version text',
            ):
                await conn.execute(text(statement))
            def migrate(connection):
                path = Path(__file__).parents[1] / 'alembic/versions/245_ohlcv_comparison_cursor.py'
                spec = importlib.util.spec_from_file_location('pr_a_migration', path)
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                with Operations.context(MigrationContext.configure(connection)):
                    module.upgrade()
            await conn.run_sync(migrate)
            uid = uuid4()
            await conn.execute(text('INSERT INTO users VALUES(:uid)'), {'uid':uid})
            await conn.execute(text("INSERT INTO ohlcv_comparison_owner(user_id,authorization_reason) VALUES(:uid,'integration fixture')"), {'uid':uid})
            await conn.execute(text("INSERT INTO config_profiles VALUES(:uid,NULL,'ohlcv_comparison',CAST(:cfg AS jsonb))"), {'uid':uid,'cfg':json.dumps(CONFIG)})
            await conn.execute(text("INSERT INTO ohlcv_capture_contracts VALUES(:contract,:start,'SHADOW',false)"), {'contract':comparison.STATE_CAPTURE_CONTRACT_VERSION,'start':START})
            for index, symbol in enumerate(('EXACT','DIFFERENT','MISSING')):
                params = dict(at=START+timedelta(minutes=index),symbol=symbol,contract=comparison.STATE_CAPTURE_CONTRACT_VERSION)
                await conn.execute(text("INSERT INTO ohlcv_shadow VALUES(:at,:symbol,'gate','1m','spot',1,1,1,1,1,1,:contract)"),params)
                if symbol != 'MISSING':
                    await conn.execute(text("INSERT INTO ohlcv VALUES(:at,:symbol,'gate','1m','spot',1,1,1,:close,1,1)"),{**params,'close':1 if symbol=='EXACT' else 2})
        yield factory, engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_incremental_equals_full_and_restart_is_idempotent(database):
    factory, engine = database
    cutoff = START + timedelta(minutes=3)
    await comparison.run_incremental(factory, now=cutoff)
    # A new factory models a fresh process; the checkpoint must live in PostgreSQL.
    await comparison.run_incremental(async_sessionmaker(engine), now=cutoff)
    assert await comparison.run_incremental(factory, now=cutoff) == []
    async with factory() as db:
        summaries = (await db.execute(text("SELECT summary FROM ohlcv_comparison_windows WHERE timeframe='1m'"))).scalars().all()
        totals = {key:sum(item[key] for item in summaries) for key in summaries[0]}
        full = dict((await db.execute(text('''SELECT count(*) AS shadow_rows,
            count(o.time) AS compared_rows,
            count(*) FILTER(WHERE o.time IS NOT NULL AND (o.open,o.high,o.low,o.close,o.volume,o.quote_volume)
                IS NOT DISTINCT FROM (s.open,s.high,s.low,s.close,s.volume,s.quote_volume)) AS exact_rows,
            count(*) FILTER(WHERE o.time IS NOT NULL AND (o.open,o.high,o.low,o.close,o.volume,o.quote_volume)
                IS DISTINCT FROM (s.open,s.high,s.low,s.close,s.volume,s.quote_volume)) AS divergent_rows,
            count(*) FILTER(WHERE o.time IS NULL) AS missing_canonical_rows
            FROM ohlcv_shadow s LEFT JOIN ohlcv o USING(time,symbol,exchange,timeframe,market_type)
            WHERE s.timeframe='1m'
        '''))).mappings().one())
        assert totals == full == dict(shadow_rows=3, compared_rows=2, exact_rows=1, divergent_rows=1, missing_canonical_rows=1)


@pytest.mark.asyncio
async def test_failure_after_snapshot_rolls_back_snapshot_and_cursor(database):
    factory, engine = database
    class FailBeforeCursor(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            if 'INSERT INTO ohlcv_comparison_cursors' in str(statement):
                raise RuntimeError('injected crash before checkpoint')
            return await super().execute(statement, *args, **kwargs)
    with pytest.raises(RuntimeError, match='injected crash'):
        await comparison.run_incremental(async_sessionmaker(engine,class_=FailBeforeCursor), now=START+timedelta(minutes=3))
    async with factory() as db:
        assert (await db.execute(text('SELECT count(*) FROM ohlcv_comparison_windows'))).scalar() == 0
        assert (await db.execute(text('SELECT count(*) FROM ohlcv_comparison_cursors'))).scalar() == 0
    assert await comparison.run_incremental(factory,now=START+timedelta(minutes=3))


@pytest.mark.asyncio
async def test_row_limit_never_certifies_truncated_window(database):
    factory, _ = database
    async with factory() as db:
        await db.execute(text("UPDATE config_profiles SET config_json=jsonb_set(config_json,'{max_shadow_rows}','1')"))
        await db.commit()
    with pytest.raises(ValueError, match='row_budget_exceeded'):
        await comparison.run_incremental(factory,now=START+timedelta(minutes=3))
    async with factory() as db:
        assert (await db.execute(text('SELECT count(*) FROM ohlcv_comparison_cursors'))).scalar() == 0


@pytest.mark.asyncio
async def test_config_missing_and_disabled_are_explicit(database):
    factory, _ = database
    async with factory() as db:
        await db.execute(text("UPDATE config_profiles SET config_json=jsonb_set(config_json,'{enabled}','false')")); await db.commit()
    assert (await comparison.run_incremental(factory))[0]['reason'] == 'operator_config_disabled'
    async with factory() as db:
        await db.execute(text('DELETE FROM config_profiles')); await db.commit()
    assert (await comparison.run_incremental(factory))[0]['reason'] == 'operator_config_missing'


@pytest.mark.asyncio
async def test_concurrent_worker_skips_locked_cursor(database):
    factory, _ = database
    async with factory() as owner:
        async with owner.begin():
            await owner.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                {'key':f'ohlcv_comparison:{comparison.STATE_CAPTURE_CONTRACT_VERSION}:1m'})
            results = await comparison.run_incremental(factory,now=START+timedelta(minutes=3))
            assert all(item['timeframe'] != '1m' for item in results)
    results = await comparison.run_incremental(factory,now=START+timedelta(minutes=3))
    assert any(item['timeframe']=='1m' and item['start']==START for item in results)


@pytest.mark.asyncio
async def test_statement_timeout_and_explicit_late_replay(database):
    from sqlalchemy.exc import DBAPIError
    factory, _ = database
    cfg = OHLCVComparisonConfig.model_validate(CONFIG)
    async with factory() as db:
        await comparison.compare_window(db,cfg,'1m',comparison.STATE_CAPTURE_CONTRACT_VERSION,START,START+timedelta(minutes=2))
        assert int((await db.execute(text("SELECT extract(epoch FROM current_setting('statement_timeout')::interval)*1000"))).scalar()) == cfg.statement_timeout_ms
    cutoff=START+timedelta(minutes=3)
    await comparison.run_incremental(factory,now=cutoff)
    await comparison.run_incremental(factory,now=cutoff)
    async with factory() as db:
        await db.execute(text("INSERT INTO ohlcv SELECT time,symbol,exchange,timeframe,market_type,open,high,low,close,volume,quote_volume FROM ohlcv_shadow WHERE symbol='MISSING'")); await db.commit()
    assert await comparison.run_incremental(factory,now=cutoff) == []
    async with factory() as db:
        result = await comparison.compare_window(db,cfg,'1m',comparison.STATE_CAPTURE_CONTRACT_VERSION,START+timedelta(minutes=2),cutoff)
        assert result['missing_canonical_rows']==0
        # Original evidence remains an as-observed snapshot, not silently rewritten.
        assert (await db.execute(text("SELECT summary->>'missing_canonical_rows' FROM ohlcv_comparison_windows WHERE timeframe='1m' ORDER BY window_start DESC LIMIT 1"))).scalar()=='1'
