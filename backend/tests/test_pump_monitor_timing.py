import asyncio
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.services import pump_monitor_timing as timing


START = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)


def row(symbol="TRX_USDT", strong=False, **extra):
    return {"symbol": symbol, "direction": {"direction": "long", "strength": "forte" if strong else "normal",
            "reason": "perp_confirma+ml_confirma" if strong else "so_spot"}, **extra}


def step(previous, value, seconds, baseline=False):
    return timing.advance(previous, value, START + timedelta(seconds=seconds), baseline=baseline)


def test_entry_strong_demotion_and_latest_reentry():
    state = step(None, row(), 0)
    assert state["monitor_entered_at"] == START
    assert state["last_strong_long_at"] is None
    state = step(state, row(strong=True), 30)
    first = state["last_strong_long_at"]
    assert first == START + timedelta(seconds=30)
    state = step(state, row(strong=True), 60)
    assert state["last_strong_long_at"] == first
    state = step(state, row(), 90)
    assert state["last_strong_long_at"] == first
    state = step(state, row(strong=True), 120)
    assert state["last_strong_long_at"] == START + timedelta(seconds=120)
    assert state["monitor_entered_at"] == START


def test_new_asset_can_arrive_strong_but_bootstrap_cannot_invent_history():
    new = step(None, row(strong=True), 0)
    assert new["last_strong_long_at"] == new["monitor_entered_at"] == START
    baseline = step(None, row(strong=True), 0, baseline=True)
    assert baseline["monitor_entered_at"] is None
    assert baseline["last_strong_long_at"] is None
    assert timing.public_timing(baseline)["timing_initial_baseline"] is True
    baseline = step(baseline, row(strong=True), 30)
    assert baseline["last_strong_long_at"] is None
    baseline = step(baseline, row(), 60)
    baseline = step(baseline, row(strong=True), 90)
    assert baseline["last_strong_long_at"] == START + timedelta(seconds=90)
    assert baseline["monitor_entered_at"] is None


@pytest.mark.parametrize("missing", [
    row(collection_error="TimeoutError"),
    row(direction=None),
    row(direction={"direction": "neutral", "strength": None, "reason": "sem_dados"}),
    row(direction={"direction": "long", "strength": None, "reason": "so_spot"}),
])
def test_collection_gaps_never_create_loss_and_reentry(missing):
    state = step(None, row(strong=True), 0)
    state = step(state, missing, 30)
    state = step(state, row(strong=True), 60)
    assert state["last_strong_long_at"] == START
    assert state["monitor_entered_at"] == START


def test_unknown_initial_direction_does_not_invent_a_transition():
    state = step(None, row(direction=None), 0, baseline=True)
    state = step(state, row(strong=True), 30)
    assert state["last_strong_long_at"] is None


def test_short_strong_is_not_green_strong_and_ml_demotion_is_respected():
    state = step(None, row(direction={"direction": "short", "strength": "forte", "reason": "vendidos_abrindo"}), 0)
    assert state["last_strong_long_at"] is None
    state = step(state, row(direction={"direction": "long", "strength": "normal", "reason": "perp_confirma+ml_contradiz"}), 30)
    assert state["last_strong_long_at"] is None
    state = step(state, row(strong=True), 60)
    assert state["last_strong_long_at"] == START + timedelta(seconds=60)


def test_duplicate_old_cycles_and_real_reentry():
    state = step(None, row(strong=True), 30)
    assert step(state, row(), 30) == state
    assert step(state, row(), 0) == state
    state["is_present"] = False
    returned = step(state, row(), 90)
    assert returned["monitor_entered_at"] == START + timedelta(seconds=90)
    assert returned["last_strong_long_at"] is None


def test_selection_and_snapshot_preserve_timestamps_without_mutating_them():
    from app.services import pump_monitor_engine as eng, pump_monitor_service as svc
    data = row(strong=True)
    data.update(timing.public_timing(step(None, data, 0)))
    data.update(indicators={}, pump_monitor_score=None, score_confidence=None,
                only_rising=True, score_components={}, alerts_active=[])
    envelope = {"rows": [data], "generated_at": START.isoformat()}
    for limit, rising, columns in [(0, False, None), (10, True, ["flow"]), (20, False, ["price"])]:
        selected = svc.select_rows(envelope, limit=limit, sort="symbol", order="asc",
                                   only_rising=rising, columns=columns, config=eng.DEFAULT_CONFIG)
        assert {k: selected["rows"][0][k] for k in timing.TIMING_FIELDS} == timing.public_timing(step(None, data, 0))
    assert {k: svc._compact(data)[k] for k in timing.TIMING_FIELDS} == timing.public_timing(step(None, data, 0))


def test_slower_older_cycle_cannot_overwrite_newer_public_timestamps_or_state():
    fakeredis = pytest.importorskip("fakeredis")
    pytest.importorskip("lupa")
    from app.services.pump_monitor_service import _PUBLISH_CYCLE
    redis = fakeredis.FakeRedis()
    newer = {"generated_at": (START + timedelta(seconds=60)).isoformat(),
             "rows": [{"last_strong_long_at": (START + timedelta(seconds=30)).isoformat()}]}
    older = {"generated_at": START.isoformat(), "rows": [{"last_strong_long_at": None}]}
    assert redis.eval(_PUBLISH_CYCLE, 2, "latest", "state", json.dumps(newer), 300, '{"cycle": "new"}', 86400) == 1
    assert redis.eval(_PUBLISH_CYCLE, 2, "latest", "state", json.dumps(older), 300, '{"cycle": "old"}', 86400) == 0
    assert json.loads(redis.get("latest")) == newer
    assert json.loads(redis.get("state")) == {"cycle": "new"}
    redis.flushall()
    assert redis.eval(_PUBLISH_CYCLE, 2, "latest", "state", json.dumps(older), 300, '{}', 86400) == 1
    assert redis.eval(_PUBLISH_CYCLE, 2, "latest", "state", json.dumps(newer), 300, '{"cycle": "new"}', 86400) == 1
    assert json.loads(redis.get("latest")) == newer


def test_postgres_migration_persistence_isolation_and_concurrency():
    """Opt-in real PostgreSQL check; every object lives in a unique test schema."""
    url = os.environ.get("PUMP_TIMING_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PUMP_TIMING_TEST_DATABASE_URL to an isolated local PostgreSQL")
    from sqlalchemy.engine import make_url
    assert make_url(url).host in ("localhost", "127.0.0.1"), "Local test database only"
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    async def scenario():
        schema = "pump_timing_test_" + uuid4().hex
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        sessions = async_sessionmaker(engine)
        user, other, pool, pool2, other_pool = [str(uuid4()) for _ in range(5)]
        spec = importlib.util.spec_from_file_location("timing_migration", Path(__file__).parents[1] / "alembic/versions/251_pump_monitor_timing.py")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        def migrate(conn, operation):
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, operation)()

        async def cycle(seconds, rows, *, uid=user, pid=pool, candidates=None, caps=None):
            async with sessions.begin() as db:
                return await timing.record_cycle(db, uid, pid, rows,
                    cycle_at=START + timedelta(seconds=seconds),
                    candidates=candidates if candidates is not None else [r["symbol"] for r in rows],
                    market_caps=caps or {})

        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE TABLE users (id uuid PRIMARY KEY)"))
                await conn.execute(text("CREATE TABLE pools (id uuid PRIMARY KEY)"))
                await conn.execute(text("INSERT INTO users VALUES (CAST(:id AS uuid))"), [{"id": user}, {"id": other}])
                await conn.execute(text("INSERT INTO pools VALUES (CAST(:id AS uuid))"), [{"id": p} for p in (pool, pool2, other_pool)])
                await conn.run_sync(lambda c: migrate(c, "upgrade"))
                await conn.run_sync(lambda c: migrate(c, "upgrade"))  # additive DDL is idempotent
            initial = await cycle(0, [row(strong=True)])
            assert initial["TRX_USDT"]["monitor_entered_at"] is None
            assert initial["TRX_USDT"]["last_strong_long_at"] is None
            await cycle(30, [row()])
            strong = await cycle(60, [row(strong=True), row("NEW_USDT", strong=True)])
            stamp = (START + timedelta(seconds=60)).isoformat()
            assert strong["TRX_USDT"]["last_strong_long_at"] == stamp
            assert strong["NEW_USDT"]["monitor_entered_at"] == stamp
            assert strong["NEW_USDT"]["last_strong_long_at"] == stamp
            # A new engine/session models process restart; no Redis is involved.
            await engine.dispose()
            restored = await cycle(90, [row(strong=True), row("NEW_USDT", strong=True)])
            assert restored == strong
            assert await cycle(90, []) is None
            assert await cycle(0, []) is None
            # Missing market metadata hides a member but cannot reset its timing.
            await cycle(120, [row()], candidates=["TRX_USDT", "NEW_USDT"])
            back = await cycle(150, [row(), row("NEW_USDT", strong=True)])
            assert back["NEW_USDT"] == strong["NEW_USDT"]
            # Explicit removal and known cap exclusion both end membership.
            await cycle(180, [], candidates=["NEW_USDT"], caps={"NEW_USDT": 1})
            back = await cycle(210, [row(strong=True), row("NEW_USDT")])
            assert back["TRX_USDT"]["monitor_entered_at"] == (START + timedelta(seconds=210)).isoformat()
            assert back["NEW_USDT"]["last_strong_long_at"] is None
            isolated = await cycle(0, [row()], uid=other, pid=other_pool)
            assert isolated["TRX_USDT"]["timing_initial_baseline"] is True
            await cycle(240, [row()], pid=pool2)
            switched_back = await cycle(270, [row()])
            assert switched_back["TRX_USDT"]["monitor_entered_at"] == (START + timedelta(seconds=270)).isoformat()
            # Hold the lock to force a newer cycle ahead of an older worker.
            async with sessions.begin() as db:
                newest = await timing.record_cycle(db, user, pool, [row(strong=True)],
                    cycle_at=START + timedelta(seconds=330), candidates=["TRX_USDT"], market_caps={})
                older = asyncio.create_task(cycle(300, [row()]))
                await asyncio.sleep(0)
            assert await older is None
            assert newest["TRX_USDT"]["last_strong_long_at"] == (START + timedelta(seconds=330)).isoformat()
            async with engine.begin() as conn:
                await conn.run_sync(lambda c: migrate(c, "downgrade"))
                assert (await conn.execute(text("SELECT to_regclass('pump_monitor_asset_timing')"))).scalar() is None
        finally:
            await engine.dispose()
            async with admin.begin() as conn:
                await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            await admin.dispose()
    asyncio.run(scenario())
