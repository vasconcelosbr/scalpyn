"""Real PostgreSQL integration; set MTF_TEST_DATABASE_URL to an isolated test DB."""
import importlib.util
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.fixture
def database():
    url = os.environ.get('MTF_TEST_DATABASE_URL')
    if not url:
        pytest.skip('MTF_TEST_DATABASE_URL is required for PostgreSQL integration')
    assert 'mtf_chart_test' in url, 'Refuse to run against a non-test database'
    engine = sa.create_engine(url)
    schema = 'mtf_test_' + uuid4().hex
    with engine.begin() as db:
        db.execute(sa.text(f'CREATE SCHEMA {schema}'))
        db.execute(sa.text(f'SET LOCAL search_path TO {schema},public'))
        db.execute(sa.text('CREATE TABLE users(id uuid PRIMARY KEY)'))
        db.execute(sa.text('''CREATE TABLE config_profiles(id uuid PRIMARY KEY,user_id uuid REFERENCES users(id),
            pool_id uuid,config_type text,config_json jsonb,is_active boolean,created_at timestamptz,updated_at timestamptz)'''))
        uid = uuid4()
        db.execute(sa.text('INSERT INTO users VALUES (:uid)'), {'uid': uid})
        path = Path(__file__).parents[1]/'alembic/versions/247_mtf_chart.py'
        spec = importlib.util.spec_from_file_location('migration_mtf_chart', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
        yield db, uid
        db.execute(sa.text(f'DROP SCHEMA {schema} CASCADE'))
    engine.dispose()


def test_online_migration_config_and_dedup(database):
    from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
    db, uid = database
    stored = db.scalar(sa.text("SELECT config_json FROM config_profiles WHERE user_id=:uid"), {'uid':uid})
    assert MTFChartConfig.model_validate(stored).model_dump() == experimental_preset()
    eid = uuid4()
    params = {'id':eid,'uid':uid,'payload':json.dumps({'mode':'SHADOW','operational_effect':False})}
    insert = sa.text('''INSERT INTO mtf_chart_evaluations(id,user_id,watchlist_id,symbol,identity,observed_at,payload,config_snapshot)
        VALUES(:id,:uid,'l3-consolidated','TEST_USDT','same-key',now(),CAST(:payload AS jsonb),'{}')
        ON CONFLICT(identity) DO NOTHING RETURNING id''')
    assert db.scalar(insert, params) == eid
    assert db.scalar(insert, {**params,'id':uuid4()}) is None
    db.execute(sa.text("INSERT INTO mtf_chart_shadow_events(evaluation_id,candle_time,state) VALUES(:id,1,'{\"status\":\"PENDING\"}')"), {'id':eid})
    assert db.scalar(sa.text('SELECT count(*) FROM mtf_chart_shadow_events')) == 1
    assert db.scalar(sa.text('SELECT count(*) FROM mtf_chart_evaluations WHERE user_id=:uid'),{'uid':uuid4()}) == 0


def test_service_persistence_shadow_isolation_and_report(database):
    import asyncio
    from app.schemas.mtf_chart import MTFChartConfig, experimental_preset
    from app.services.mtf_chart_service import persist_evaluation, append_shadow
    from app.services.mtf_chart_engine import evaluate
    from test_mtf_chart_engine import setup_frames
    from app.api.mtf_chart import report, history
    from unittest.mock import patch, AsyncMock
    from datetime import datetime, timezone
    db, uid = database
    class Adapter:
        async def scalar(self, statement, params=None): return db.scalar(statement, params)
        async def execute(self, statement, params=None): return db.execute(statement, params)
    async def check():
        cfg = MTFChartConfig.model_validate(experimental_preset())
        payload = evaluate('BTC_USDT', setup_frames(), cfg, 28800, .01, 'UP')
        payload.update(config_hash='cfg', profile_id=None, profile_hash=None, indicator_identity={}, observed_at_utc=datetime.now(timezone.utc).isoformat())
        assert await persist_evaluation(Adapter(),uid,'l3-consolidated',{'analysis':payload},cfg)
        assert not await persist_evaluation(Adapter(),uid,'l3-consolidated',{'analysis':payload},cfg)
        assert db.scalar(sa.text('SELECT count(*) FROM mtf_chart_shadow_events')) == 1
        with patch('app.api.mtf_chart.config_or_error', AsyncMock(return_value=cfg)):
            h = await history('BTC_USDT','l3-consolidated',db=Adapter(),uid=uid)
            assert len(h['items']) == 1 and h['items'][0]['shadow']['status'] == 'PENDING'
            r = await report('l3-consolidated',db=Adapter(),uid=uid)
            assert r['groups'] == [] and r['operational_effect'] is False
            eid = db.scalar(sa.text('SELECT id FROM mtf_chart_evaluations'))
            await append_shadow(Adapter(),eid,{'last_candle':600,'status':'SL','pnl_pct':-1.25,'pnl_usdt':-125,'ambiguous':True})
            r = await report('l3-consolidated',db=Adapter(),uid=uid)
            assert r['groups'][0]['n'] == 1
            assert r['groups'][0]['mean_pct'] == -1.25
            assert r['groups'][0]['ambiguous'] == 1
            assert r['groups'][0]['confidence_interval'] is None
            assert not r['groups'][0]['sample_sufficient']
            other = await report('l3-consolidated',db=Adapter(),uid=uuid4())
            assert other['groups'] == []
    asyncio.run(check())
