"""Real PostgreSQL/asyncpg regressions for the monitor's SQL recovery.

Set SHADOW_MONITOR_TEST_DATABASE_URL to an isolated local PostgreSQL database
named scalpyn_monitor_test. Every test uses and removes its own schema.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Column, MetaData, Table, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database
from app.models.shadow_trade import ShadowTrade
from app.tasks import shadow_trade_monitor as monitor


@pytest_asyncio.fixture
async def postgres():
    database_url = os.environ.get("SHADOW_MONITOR_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SHADOW_MONITOR_TEST_DATABASE_URL is not configured")
    parsed = make_url(database_url)
    if parsed.host not in {"127.0.0.1", "localhost", "::1"} or (
        parsed.database != "scalpyn_monitor_test"
    ):
        pytest.fail("Monitor integration tests require an isolated local test database")
    schema = f"monitor_test_{uuid4().hex}"
    engine = create_async_engine(
        parsed.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": schema}},
    )
    # Mirror the real ORM column types without unrelated production FKs.
    metadata = MetaData()
    shadow_table = Table(
        "shadow_trades", metadata,
        *(Column(c.name, c.type, primary_key=c.primary_key)
          for c in ShadowTrade.__table__.columns),
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(metadata.create_all)
            await connection.execute(text("""
                CREATE TABLE ohlcv (
                    symbol text, timeframe text, time timestamptz,
                    high numeric, low numeric, close numeric
                )
            """))
            await connection.execute(text("""
                CREATE TABLE market_metadata (
                    symbol text PRIMARY KEY, price numeric, last_updated timestamptz
                )
            """))
            await connection.execute(text("""
                CREATE TABLE shadow_trade_closure_audit (
                    shadow_trade_id uuid, source text, symbol text,
                    previous_status text, entry_price numeric, exit_price numeric,
                    tp_price numeric, sl_price numeric, pnl_pct numeric,
                    pnl_usdt numeric, closure_reason text, closer_run_id uuid
                )
            """))
        yield engine, async_sessionmaker(engine, expire_on_commit=False), shadow_table
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_cutoff", [True, False])
async def test_atr_timestamp_is_typed_and_future_candles_are_excluded(postgres, use_cutoff):
    _, sessions, _ = postgres
    entry_at = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    async with sessions() as db, db.begin():
        await db.execute(text("""
            INSERT INTO ohlcv (symbol, timeframe, time, high, low, close)
            VALUES ('TEST_USDT', '5m', :before, 102, 98, 100),
                   ('TEST_USDT', '5m', :entry, 103, 99, 101),
                   ('TEST_USDT', '5m', :after, 1000, 0, 500),
                   ('TEST_USDT', '1m', :entry, 9000, 0, 100)
        """), {
            "before": entry_at - timedelta(minutes=5),
            "entry": entry_at,
            "after": entry_at + timedelta(minutes=5),
        })
        result = await monitor._compute_atr_pct(
            db, "TEST_USDT", 100, period=1, timeframe="5m",
            as_of=entry_at if use_cutoff else None,
        )
        assert result == pytest.approx(4.0 if use_cutoff else 1000.0)
        assert await db.scalar(text("SELECT 42")) == 42


@pytest.mark.asyncio
async def test_optional_atr_sql_failure_restores_transaction(postgres, monkeypatch):
    _, sessions, _ = postgres
    async with sessions() as db, db.begin():
        await db.execute(text("""
            INSERT INTO market_metadata(symbol, price) VALUES ('KEPT_USDT', 1)
        """))
        execute = db.execute

        async def fail_atr_read(statement, *args, **kwargs):
            if "SELECT high, low, close" in str(statement):
                return await execute(text("SELECT 1 / 0"))
            return await execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, "execute", fail_atr_read)
        assert await monitor._compute_atr_pct(db, "TEST_USDT", 100) is None
        assert db.is_active
        assert await db.scalar(text("SELECT 42")) == 42
    async with sessions() as db:
        assert await db.scalar(text("SELECT count(*) FROM market_metadata")) == 1


@pytest.mark.asyncio
async def test_atr_does_not_swallow_failure_before_nested_savepoint(postgres):
    _, sessions, _ = postgres
    async with sessions() as db:
        with pytest.raises(DBAPIError):
            async with db.begin():
                # begin_nested() flushes pending ORM changes before SAVEPOINT.
                db.add(ShadowTrade(id=uuid4(), symbol="X" * 100, status="RUNNING"))
                await monitor._compute_atr_pct(db, "TEST_USDT", 100)


@pytest.mark.asyncio
async def test_fast_scan_recovers_expired_id_and_commits_healthy_row(
    postgres, monkeypatch, caplog,
):
    engine, sessions, shadow_table = postgres
    bad_id, good_id = UUID(int=1), UUID(int=2)
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        await connection.execute(insert(shadow_table), [
            {
                "id": shadow_id, "symbol": symbol, "source": "L3_LAB",
                "status": "RUNNING", "entry_price": 100,
                "entry_timestamp": now - timedelta(hours=1),
                "created_at": now - timedelta(hours=1),
                "tp_price": 101, "sl_price": 99, "config_snapshot": {},
            }
            for shadow_id, symbol in [(bad_id, "BAD_USDT"), (good_id, "GOOD_USDT")]
        ])
        await connection.execute(text("""
            INSERT INTO market_metadata(symbol, price, last_updated)
            VALUES ('BAD_USDT', 102, :now), ('GOOD_USDT', 102, :now)
        """), {"now": now})

    visited = []

    async def advance(db, shadow, _policy, **_kwargs):
        visited.append(shadow.id)
        if shadow.id == bad_id:
            shadow.symbol = "DIRTY_USDT"
            await db.execute(text("SELECT 1 / 0"))
        shadow.status = "COMPLETED"
        shadow.outcome = "TP_HIT"
        shadow.exit_price = 101
        return "completed"

    monkeypatch.setattr(database, "CeleryAsyncSessionLocal", sessions)
    monkeypatch.setattr(monitor, "_advance_shadow", advance)
    monkeypatch.setattr(monitor, "_load_shadow_monitor_ops_config", AsyncMock(
        return_value={"fast_scan_priority": "AGE", "fast_scan_batch_size": 5},
    ))
    monkeypatch.setattr(monitor, "_run_best_effort_budgeted", AsyncMock(return_value=(0, 0)))
    result = await monitor._fast_barrier_scan_async(str(uuid4()))

    assert visited == [bad_id, good_id]
    assert result["fast_scan_errors"] == 1
    assert result["fast_scan_closed_tp"] == 1
    assert "MissingGreenlet" not in caplog.text
    assert "fast-scan failed run_id" not in caplog.text
    async with sessions() as db:
        rows = (await db.execute(text(
            "SELECT id, symbol, status FROM shadow_trades ORDER BY id"
        ))).all()
        assert rows == [
            (bad_id, "BAD_USDT", "RUNNING"),
            (good_id, "GOOD_USDT", "COMPLETED"),
        ]
        assert await db.scalar(text("SELECT count(*) FROM shadow_trade_closure_audit")) == 1
