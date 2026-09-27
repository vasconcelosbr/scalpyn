"""Radar lifecycle regressions against a real, isolated PostgreSQL schema.

Set SHADOW_MONITOR_TEST_DATABASE_URL to the local scalpyn_monitor_test database.
These exercise the production reconciler and its real watchlist cascade SQL.
"""
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Column, MetaData, Table, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.pool import Pool, PoolAssetExclusion, PoolCoin
from app.services.pool_service import radar_pool_coin_is_candidate
from app.services.radar_pool_sync import operator_pool_overrides, reconcile_radar_pool
from app.services.radar_service import validated_radar_assets


@pytest_asyncio.fixture
async def radar_db():
    database_url = os.environ.get("SHADOW_MONITOR_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SHADOW_MONITOR_TEST_DATABASE_URL is not configured")
    parsed = make_url(database_url)
    if parsed.host not in {"127.0.0.1", "localhost", "::1"} or (
        parsed.database != "scalpyn_monitor_test"
    ):
        pytest.fail("Radar integration tests require an isolated local test database")
    schema = f"radar_sync_test_{uuid4().hex}"
    engine = create_async_engine(
        parsed.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    metadata = MetaData()
    for model in (Pool, PoolCoin, PoolAssetExclusion):
        Table(
            model.__tablename__, metadata,
            *(Column(c.name, c.type, primary_key=c.primary_key)
              for c in model.__table__.columns),
        )
    ids = {key: uuid4() for key in (
        "user", "pump", "spot", "pump_root", "pump_l3", "spot_root",
    )}
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(metadata.create_all)
            await connection.execute(text("""
                CREATE TABLE shadow_trades (
                    id uuid PRIMARY KEY, user_id uuid, symbol text,
                    status text, source text, config_snapshot jsonb
                )
            """))
            await connection.execute(text("""
                CREATE TABLE pipeline_watchlists (
                    id uuid PRIMARY KEY, source_pool_id uuid,
                    source_watchlist_id uuid
                )
            """))
            await connection.execute(text("""
                CREATE TABLE pipeline_watchlist_assets (
                    watchlist_id uuid, symbol text, level_direction text,
                    level_change_at timestamptz
                )
            """))
        async with sessions() as db, db.begin():
            db.add_all([
                Pool(id=ids["pump"], user_id=ids["user"], name="PUMP",
                     overrides={"radar_enabled": True}),
                Pool(id=ids["spot"], user_id=ids["user"], name="POOLSPOT",
                     overrides={"auto_refresh_enabled": True}),
            ])
            await db.execute(text("""
                INSERT INTO pipeline_watchlists
                    (id, source_pool_id, source_watchlist_id)
                VALUES (:pump_root, :pump, NULL), (:pump_l3, NULL, :pump_root),
                       (:spot_root, :spot, NULL)
            """), ids)
            await db.execute(text("""
                INSERT INTO pipeline_watchlist_assets
                    (watchlist_id, symbol, level_direction)
                VALUES (:pump_root, 'BTC_USDT', 'up'),
                       (:pump_l3, 'BTC_USDT', 'up'),
                       (:spot_root, 'BTC_USDT', 'up')
            """), ids)
        yield sessions, ids
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def _sync(sessions, ids, pairs, **kwargs):
    async with sessions() as db, db.begin():
        return await reconcile_radar_pool(
            db, pool_id=ids["pump"], user_id=ids["user"], radar_pairs=pairs,
            **kwargs,
        )


async def _coins(sessions, pool_id):
    async with sessions() as db:
        return (await db.scalars(select(PoolCoin).where(
            PoolCoin.pool_id == pool_id,
        ))).all()


async def _open_shadow(db, ids, symbol, source="L3", status="RUNNING", user_id=None):
    shadow_id = uuid4()
    await db.execute(text("""
        INSERT INTO shadow_trades
            (id, user_id, symbol, source, status, config_snapshot)
        VALUES (:id, :user_id, :symbol, :source, :status, '{"immutable":"entry"}')
    """), {"id": shadow_id, "user_id": user_id or ids["user"],
           "symbol": symbol, "source": source, "status": status})
    return shadow_id


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [
    "L3", "L3_LAB", "L3_REJECTED", "L3_SIMULATED", "L1_SPECTRUM",
    "STRATEGY_LAB", "CUSTOM_OBSERVATION",
])
@pytest.mark.parametrize("status", ["PENDING", "RUNNING"])
async def test_radar_lifecycle_retains_open_shadow_and_reentry_identity(
    radar_db, source, status,
):
    sessions, ids = radar_db
    assert (await _sync(sessions, ids, {"BTC_USDT"}))["added"] == 1
    first = (await _coins(sessions, ids["pump"]))[0]
    assert first.origin == "radar" and not first.held_for_open_position
    assert not first.is_tradable and not first.is_approved
    async with sessions() as db, db.begin():
        shadow_id = await _open_shadow(db, ids, "BTC_USDT", source, status)

    assert (await _sync(sessions, ids, set()))["held"] == 1
    held = (await _coins(sessions, ids["pump"]))[0]
    assert held.id == first.id and held.is_active and held.held_for_open_position
    async with sessions() as db:
        directions = dict((await db.execute(text("""
            SELECT watchlist_id, level_direction FROM pipeline_watchlist_assets
        """))).all())
        assert directions == {
            ids["pump_root"]: "down", ids["pump_l3"]: "down", ids["spot_root"]: "up",
        }

    await _sync(sessions, ids, {"BTC_USDT"})
    returned = (await _coins(sessions, ids["pump"]))[0]
    assert returned.id == first.id and not returned.held_for_open_position
    async with sessions() as db, db.begin():
        candidates = (await db.scalars(select(PoolCoin.symbol).join(
            Pool, Pool.id == PoolCoin.pool_id,
        ).where(Pool.id == ids["pump"], radar_pool_coin_is_candidate(Pool, PoolCoin)))).all()
        assert candidates == ["BTC_USDT"]
        shadows = (await db.execute(text("""
            SELECT id, status, source, config_snapshot FROM shadow_trades
        """))).all()
        assert shadows == [(shadow_id, status, source, {"immutable": "entry"})]
        await db.execute(text("UPDATE shadow_trades SET status = 'COMPLETED' WHERE id = :id"),
                         {"id": shadow_id})

    assert (await _sync(sessions, ids, set()))["removed"] == 1
    assert await _coins(sessions, ids["pump"]) == []
    async with sessions() as db:
        assert (await db.execute(text("""
            SELECT id, status, config_snapshot FROM shadow_trades
        """))).all() == [(shadow_id, "COMPLETED", {"immutable": "entry"})]


@pytest.mark.asyncio
async def test_minute_signals_with_partial_global_metadata_drive_membership(radar_db):
    """The endpoint's live list remains authoritative with global collection off."""
    sessions, ids = radar_db
    observed_at = datetime(2026, 9, 27, 16, 39, tzinfo=timezone.utc)

    def selection(*pairs):
        # Metadata follows the captured minute-signals response, whose ACTIVE
        # rows coexist with market_data_enabled=false and coverage=PARTIAL.
        payload = {
            "data": [
                {"pair": pair, "status": "ACTIVE", "updated_at": observed_at.isoformat()}
                for pair in pairs
            ],
            "meta": {
                "market_data_enabled": False,
                "coverage_status": "PARTIAL",
                "has_more": False,
                "source_provider": "gate.io",
                "market": "spot",
                "as_of": observed_at.isoformat(),
                "count": len(pairs),
            },
        }
        return {asset["pair"] for asset in validated_radar_assets(payload)}

    async def candidates():
        async with sessions() as db:
            return (await db.scalars(select(PoolCoin.symbol).join(
                Pool, Pool.id == PoolCoin.pool_id,
            ).where(
                Pool.id == ids["pump"], radar_pool_coin_is_candidate(Pool, PoolCoin),
            ))).all()

    async with sessions() as db, db.begin():
        await db.execute(text("""
            UPDATE pipeline_watchlist_assets SET symbol = 'NEAR_USDT'
            WHERE watchlist_id IN (:pump_root, :pump_l3)
        """), ids)

    assert (await _sync(sessions, ids, selection("NEAR_USDT")))["added"] == 1
    first = (await _coins(sessions, ids["pump"]))[0]
    assert first.is_active and not first.held_for_open_position
    assert await candidates() == ["NEAR_USDT"]
    async with sessions() as db, db.begin():
        shadow_id = await _open_shadow(db, ids, "NEAR_USDT")
        original_shadow = (await db.execute(text("SELECT * FROM shadow_trades"))).all()

    # A complete empty minute-signals list removes candidacy even though the
    # same global metadata is still partial. The open trade keeps collection.
    assert (await _sync(sessions, ids, selection()))["held"] == 1
    held = (await _coins(sessions, ids["pump"]))[0]
    assert held.id == first.id and held.is_active and held.held_for_open_position
    assert await candidates() == []
    async with sessions() as db:
        assert dict((await db.execute(text("""
            SELECT watchlist_id, level_direction FROM pipeline_watchlist_assets
        """))).all()) == {
            ids["pump_root"]: "down", ids["pump_l3"]: "down", ids["spot_root"]: "up",
        }
        assert (await db.execute(text("SELECT * FROM shadow_trades"))).all() == original_shadow

    # Reentry restores eligibility for the ordinary watchlist scan without
    # replacing the retained pool row or creating/changing the open Shadow.
    assert (await _sync(sessions, ids, selection("NEAR_USDT")))["added"] == 0
    returned = (await _coins(sessions, ids["pump"]))[0]
    assert returned.id == first.id and returned.is_active and not returned.held_for_open_position
    assert await candidates() == ["NEAR_USDT"]
    async with sessions() as db:
        assert (await db.execute(text("SELECT * FROM shadow_trades"))).all() == original_shadow
        assert original_shadow[0].id == shadow_id
        pool = await db.get(Pool, ids["pump"])
        assert pool.overrides["radar_feed_health"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_unavailable_retains_radar_collection_but_healthy_empty_removes(radar_db):
    sessions, ids = radar_db
    healthy_at = datetime(2026, 9, 27, 3, tzinfo=timezone.utc)
    await _sync(sessions, ids, {"BTC_USDT"}, now=healthy_at)
    async with sessions() as db, db.begin():
        db.add_all([
            PoolCoin(pool_id=ids["pump"], symbol="INVALID_USDT", origin="discovered"),
            PoolCoin(pool_id=ids["pump"], symbol="OPEN_USDT", origin="discovered"),
        ])
        await _open_shadow(db, ids, "OPEN_USDT", source="L3_REJECTED")
    unavailable_at = healthy_at + timedelta(minutes=1)
    stats = await _sync(sessions, ids, None, reason="market_data_unavailable", now=unavailable_at)
    assert stats["removed"] == 1 and stats["held"] == 1
    coins = {coin.symbol: coin for coin in await _coins(sessions, ids["pump"])}
    assert set(coins) == {"BTC_USDT", "OPEN_USDT"}
    assert coins["BTC_USDT"].is_active and not coins["BTC_USDT"].held_for_open_position
    assert coins["OPEN_USDT"].is_active and coins["OPEN_USDT"].held_for_open_position
    async with sessions() as db:
        pool = await db.get(Pool, ids["pump"])
        assert pool.overrides["radar_feed_health"] == {
            "status": "unavailable", "checked_at": unavailable_at.isoformat(),
            "last_success_at": healthy_at.isoformat(), "reason": "market_data_unavailable",
        }
        assert (await db.scalars(select(PoolCoin.symbol).join(
            Pool, Pool.id == PoolCoin.pool_id,
        ).where(Pool.id == ids["pump"], radar_pool_coin_is_candidate(Pool, PoolCoin)))).all() == []
    await _sync(sessions, ids, set())
    assert [coin.symbol for coin in await _coins(sessions, ids["pump"])] == ["OPEN_USDT"]


@pytest.mark.asyncio
async def test_duplicate_cleanup_preserves_radar_identity_and_operator_permissions(radar_db):
    sessions, ids = radar_db
    radar_id = uuid4()
    async with sessions() as db, db.begin():
        db.add_all([
            PoolCoin(id=radar_id, pool_id=ids["pump"], symbol="BTC_USDT", origin="radar"),
            PoolCoin(pool_id=ids["pump"], symbol="BTC_USDT", origin="discovered",
                     is_approved=True, is_tradable=True),
        ])
    assert (await _sync(sessions, ids, {"BTC_USDT"}))["duplicates"] == 1
    coins = await _coins(sessions, ids["pump"])
    assert len(coins) == 1 and coins[0].id == radar_id
    assert coins[0].is_approved and coins[0].is_tradable
    assert (await _sync(sessions, ids, {"BTC_USDT"}))["duplicates"] == 0


@pytest.mark.asyncio
async def test_exclusions_override_feed_and_other_user_shadow_does_not_retain(radar_db):
    sessions, ids = radar_db
    symbols = {"BTC_USDT", "BLOCKED_USDT", "NOTADDED_USDT"}
    async with sessions() as db, db.begin():
        db.add_all([PoolAssetExclusion(pool_id=ids["pump"], symbol=s) for s in symbols])
        db.add_all([PoolCoin(pool_id=ids["pump"], symbol=s, origin="radar")
                    for s in {"BTC_USDT", "BLOCKED_USDT"}])
        await _open_shadow(db, ids, "BTC_USDT")
        await _open_shadow(db, ids, "BLOCKED_USDT", user_id=uuid4())
    await _sync(sessions, ids, symbols)
    coins = await _coins(sessions, ids["pump"])
    assert len(coins) == 1 and coins[0].symbol == "BTC_USDT"
    assert coins[0].is_active and coins[0].held_for_open_position


@pytest.mark.asyncio
async def test_poolspot_is_untouched_by_radar_reconciliation(radar_db):
    sessions, ids = radar_db
    async with sessions() as db, db.begin():
        coin = PoolCoin(pool_id=ids["spot"], symbol="BTC_USDT", origin="discovered",
                        is_approved=True, is_tradable=True)
        db.add(coin)
        await db.flush()
        before = {c.name: getattr(coin, c.name) for c in PoolCoin.__table__.columns}
    async with sessions() as db, db.begin():
        assert (await reconcile_radar_pool(
            db, pool_id=ids["spot"], user_id=ids["user"], radar_pairs=set(),
        ))["skipped"]
    after = (await _coins(sessions, ids["spot"]))[0]
    assert {c.name: getattr(after, c.name) for c in PoolCoin.__table__.columns} == before
    async with sessions() as db:
        assert (await db.get(Pool, ids["spot"])).overrides == {"auto_refresh_enabled": True}


@pytest.mark.parametrize("enabled", [True, False])
def test_operator_cannot_write_or_replay_worker_feed_health(enabled):
    health = {"status": "unavailable", "reason": "provider_down"}
    current = {"radar_enabled": True, "radar_feed_health": health}
    requested = {"radar_enabled": enabled, "radar_feed_health": {"status": "healthy"}}
    merged = operator_pool_overrides(current, requested)
    assert merged.get("radar_feed_health") == (health if enabled else None)
    assert current["radar_feed_health"] == health
    assert "radar_feed_health" not in operator_pool_overrides(None, requested)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["pool_patch", "overrides_put"])
async def test_settings_routes_persist_only_worker_health(radar_db, route):
    from app.api.pools import create_pool, update_pool, update_pool_overrides

    sessions, ids = radar_db
    forged = {"radar_enabled": True, "radar_feed_health": {"status": "healthy"}}
    async with sessions() as db:
        created = await create_pool(
            {"name": "NEW_RADAR", "overrides": forged}, db=db, user_id=ids["user"],
        )
        assert "radar_feed_health" not in created["overrides"]
    await _sync(sessions, ids, None, reason="provider_down")
    async with sessions() as db:
        original_health = (await db.get(Pool, ids["pump"])).overrides["radar_feed_health"]

    async def save(overrides):
        async with sessions() as db:
            if route == "pool_patch":
                return await update_pool(
                    ids["pump"], {"overrides": overrides}, db=db, user_id=ids["user"],
                )
            return await update_pool_overrides(
                ids["pump"], overrides, db=db, user_id=ids["user"],
            )

    assert (await save(forged))["overrides"]["radar_feed_health"] == original_health
    await save({"radar_enabled": False, "radar_feed_health": {"status": "healthy"}})
    await save(forged)
    async with sessions() as db:
        persisted = (await db.get(Pool, ids["pump"])).overrides
        assert persisted == {"radar_enabled": True}
