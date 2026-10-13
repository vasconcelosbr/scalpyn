"""Real PostgreSQL acceptance tests. Only an explicitly supplied local DB is used."""
import asyncio
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from pydantic import ValidationError
from app.schemas.storage_retention import StorageRetentionConfig
from app.services.storage_retention import TABLES, pack_archive, run_retention, selection_sql


def test_allowlist_and_fail_closed_config():
    for table in ("shadow_trades", "ohlcv", "indicators", "pump_opportunity_labels", "x;DROP TABLE users"):
        with pytest.raises(ValueError): selection_sql(table)
    for payload in ({"archive_before_delete": False}, {"tables": ["shadow_trades"]}, {"batch_rows": 10001}, {"enabled": "true"}):
        with pytest.raises(ValidationError): StorageRetentionConfig.model_validate(payload)
    raw = '{"price":0.123456789012345678901234567890}'
    archive, digest, size = pack_archive([{"data": raw}])
    assert gzip.decompress(archive) == ("[" + raw + "]").encode()
    assert hashlib.sha256(gzip.decompress(archive)).hexdigest() == digest
    assert size == len(gzip.decompress(archive))


def test_postgres_retention_protection_restore_and_rollback():
    url = os.getenv("RETENTION_TEST_DATABASE_URL")
    if not url: pytest.skip("RETENTION_TEST_DATABASE_URL not configured")
    assert urlparse(url).hostname in {"127.0.0.1", "localhost", "::1"}, "Only isolated local PostgreSQL allowed"
    asyncio.run(exercise_database(url))


async def exercise_database(url):
    import asyncpg
    conn = await asyncpg.connect(url)
    schema = "retention_test_" + uuid4().hex
    owner, other = uuid4(), uuid4()
    try:
        await conn.execute(f"CREATE SCHEMA {schema}")
        await conn.execute(f"SET search_path TO {schema}")
        spec = importlib.util.spec_from_file_location("retention_migration", Path(__file__).parents[1] / "alembic/versions/244_storage_retention.py")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        statements = []
        migration.op = SimpleNamespace(execute=lambda sql: statements.append(str(sql)))
        migration.upgrade()
        for sql in statements: await conn.execute(sql)
        await conn.execute("""
            CREATE TABLE config_profiles(user_id uuid,pool_id uuid,config_type text,config_json jsonb,is_active boolean,updated_at timestamptz DEFAULT now());
            CREATE TABLE ohlcv_state_ingestion_observations(
              observed_at timestamptz, symbol text,timeframe text,capture_contract_version text,
              status text,error_code text,rejected_from_closed_rows int,availability_lag_seconds numeric,
              PRIMARY KEY(observed_at,symbol,timeframe,capture_contract_version));
            CREATE TABLE ohlcv_settlement_latency_samples(
              symbol text,timeframe text,candle_open_time timestamptz,delay_target_seconds int,
              observed_at timestamptz,found bool,is_closed bool,close numeric,
              PRIMARY KEY(symbol,timeframe,candle_open_time,delay_target_seconds));
            CREATE TABLE pump_opportunity_label_queue(observation_id uuid,user_id uuid,label_spec_hash text,horizon_minutes int,
              ready_at timestamptz,completed_at timestamptz,resource_block jsonb,PRIMARY KEY(observation_id,label_spec_hash,horizon_minutes));
            CREATE TABLE pump_opportunity_labels(observation_id uuid,label_spec_hash text,horizon_minutes int,payload jsonb,
              PRIMARY KEY(observation_id,label_spec_hash,horizon_minutes));
            CREATE TABLE shadow_trades(id uuid, price numeric);
            INSERT INTO shadow_trades VALUES('11111111-1111-1111-1111-111111111111',123.456789123456789);
            CREATE INDEX ix_retention_ingestion_ok ON ohlcv_state_ingestion_observations(observed_at,symbol,timeframe,capture_contract_version) WHERE status='ok' AND error_code IS NULL AND rejected_from_closed_rows=0;
            CREATE INDEX ix_retention_completed_labels ON pump_opportunity_label_queue(user_id,completed_at,observation_id,label_spec_hash,horizon_minutes) WHERE completed_at IS NOT NULL AND resource_block IS NULL;
        """)
        for symbol, state, err, reject in [("ok", "ok", None, 0), ("error", "error", "bad", 0), ("rejected", "ok", None, 1), ("held", "ok", None, 0), ("stale", "ok", None, 0)]:
            await conn.execute("INSERT INTO ohlcv_state_ingestion_observations VALUES(now()-interval '10 days',$1,'1m','v1',$2,$3,$4,0.123456789012345678901234567890)", symbol, state, err, reject)
            if symbol != "stale":
                await conn.execute("INSERT INTO ohlcv_state_ingestion_observations VALUES(now(),$1,'1m','v1','ok',NULL,0,1)", symbol)
        await conn.execute("INSERT INTO storage_retention_holds VALUES($1,$2,'held',now()-interval '11 days',now()-interval '9 days','incident',now())", uuid4(), TABLES[0])
        for symbol, found, closed in [("ok", True, True), ("missing", False, True), ("live", True, False), ("stale", True, True)]:
            await conn.execute("INSERT INTO ohlcv_settlement_latency_samples VALUES($1,'1m',now()-interval '10 days',5,now()-interval '10 days',$2,$3,0.123456789012345678901234567890)", symbol, found, closed)
            if symbol != "stale":
                await conn.execute("INSERT INTO ohlcv_settlement_latency_samples VALUES($1,'1m',now(),5,now(),true,true,99)", symbol)
        for case in ("ok", "pending", "blocked", "missing_label", "other_owner", "different_horizon", "different_spec"):
            oid = uuid4()
            completed = None if case == "pending" else await conn.fetchval("SELECT now()-interval '10 days'")
            await conn.execute("INSERT INTO pump_opportunity_label_queue VALUES($1,$2,'v1',10,now()-interval '11 days',$3,$4::jsonb)", oid, other if case == "other_owner" else owner, completed, '{}' if case == "blocked" else None)
            if case != "missing_label":
                await conn.execute("INSERT INTO pump_opportunity_labels VALUES($1,$2,$3,'{}')", oid, "v2" if case == "different_spec" else "v1", 15 if case == "different_horizon" else 10)
        cfg = StorageRetentionConfig(enabled=True, mode="execute", batch_rows=100)
        await conn.execute("INSERT INTO storage_retention_owners(user_id,authorization_reason) VALUES($1,'test')", owner)
        await conn.execute("INSERT INTO config_profiles VALUES($1,NULL,'storage_retention',$2::jsonb,true,now())", owner, cfg.model_dump_json())
        before = {t: await conn.fetchval(f"SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text FROM {t} t") for t in (*TABLES, "pump_opportunity_labels", "shadow_trades")}
        assert (await run_retention(conn, owner, force=True))["status"] == "dry_run_required"
        dry = await run_retention(conn, owner, force=True, preview=True)
        assert dry["status"] == "completed" and dry["deleted"] == 0
        # Healthy collection with open candles is eligible too. Errors, holds,
        # missing fresh evidence, pending work and all training data stay intact.
        assert [b["candidates"] for b in dry["batches"]] == [2,1,1]
        assert (await run_retention(conn, owner))["status"] == "not_due"
        # A second connection holding the model lock blocks cleanup.
        lock_conn = await asyncpg.connect(url)
        await lock_conn.execute("SELECT pg_advisory_lock(hashtextextended($1,0))", f"pump_ml:{owner}")
        assert (await run_retention(conn, owner, force=True))["status"] == "busy"
        await lock_conn.close()
        # Archive failure must roll back the source DELETE, not lose its rows.
        await conn.execute("ALTER TABLE storage_retention_batches ADD CONSTRAINT fail_archive CHECK(deleted_count=0) NOT VALID")
        with pytest.raises(asyncpg.CheckViolationError): await run_retention(conn, owner, force=True)
        assert await conn.fetchval(f"SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text FROM {TABLES[0]} t") == before[TABLES[0]]
        await conn.execute("ALTER TABLE storage_retention_batches DROP CONSTRAINT fail_archive")
        result = await run_retention(conn, owner, force=True)
        assert result["deleted"] == 4 and result["status"] == "completed"
        for table in ("pump_opportunity_labels", "shadow_trades"):
            assert await conn.fetchval(f"SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text FROM {table} t") == before[table]
        for batch in await conn.fetch("SELECT * FROM storage_retention_batches WHERE deleted_count>0"):
            raw = gzip.decompress(batch["archive_gzip"])
            assert hashlib.sha256(raw).hexdigest() == batch["archive_sha256"]
            table = batch["table_name"]
            await conn.execute(f"INSERT INTO {table} SELECT * FROM jsonb_populate_recordset(NULL::{table},$1::jsonb)", raw.decode())
            assert await conn.fetchval(f"SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text FROM {table} t") == before[table]
        limited = cfg.model_copy(update={"batch_rows": 1, "max_batches_per_run": 3})
        await conn.execute("UPDATE config_profiles SET config_json=$1::jsonb", limited.model_dump_json())
        assert (await run_retention(conn, owner, force=True))["status"] == "dry_run_required"
        await run_retention(conn, owner, force=True, preview=True)
        capped = await run_retention(conn, owner, force=True)
        assert len(capped["batches"]) == 3 and capped["deleted"] == 3
        assert capped["status"] == "batch_budget"
    finally:
        await conn.execute(f"DROP SCHEMA {schema} CASCADE")
        await conn.close()
