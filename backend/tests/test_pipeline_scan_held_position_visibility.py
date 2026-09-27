"""Radar membership regressions using PUMP_ELIGIBILITY_TEST_DATABASE_URL.

Only a local scalpyn_monitor_test DB is accepted; each test owns its schema.
"""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Column, MetaData, Table, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.watchlists import _intersect_assets_with_active_parent
from app.models.backoffice import DecisionLog, L3AuthorizationOutbox
from app.models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from app.models.pool import Pool, PoolCoin
from app.models.profile import Profile
from app.models.shadow_trade import ShadowTrade
from app.services.pipeline_live_candidates import load_live_l3_rejections, load_recently_authorized_l3_shadows
from app.services.pool_service import (
    get_active_pool_symbols, load_radar_watchlist_eligibility, radar_shadow_entry_is_eligible,
)


@pytest_asyncio.fixture
async def postgres():
    url = os.environ.get("PUMP_ELIGIBILITY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PUMP_ELIGIBILITY_TEST_DATABASE_URL is not configured")
    parsed = make_url(url)
    if parsed.host not in {"127.0.0.1", "localhost", "::1"} or parsed.database != "scalpyn_monitor_test":
        pytest.fail("Radar integration tests require the isolated local test database")
    schema = f"radar_eligibility_{uuid4().hex}"
    engine = create_async_engine(parsed.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": schema}})
    metadata = MetaData()
    models = (Pool, PoolCoin, Profile, PipelineWatchlist, PipelineWatchlistAsset,
              ShadowTrade, DecisionLog, L3AuthorizationOutbox)
    tables = {model: Table(model.__tablename__, metadata, *(
        Column(c.name, c.type, primary_key=c.primary_key) for c in model.__table__.columns
    )) for model in models}
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False), tables
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def seed_chain(db, tables, *, radar=True):
    now = datetime.now(timezone.utc)
    user, pool, coin = uuid4(), uuid4(), uuid4()
    watchlists, profiles = [uuid4() for _ in range(4)], [uuid4() for _ in range(4)]
    overrides = {"radar_enabled": radar, "radar_feed_health": {"status": "healthy"}}
    await db.execute(insert(tables[Pool]).values(
        id=pool, user_id=user, name="PUMP" if radar else "POOLSPOT",
        market_type="spot", is_active=True, overrides=overrides))
    await db.execute(insert(tables[PoolCoin]).values(
        id=coin, pool_id=pool, symbol="TEST_USDT", market_type="spot", is_active=True,
        held_for_open_position=False, origin="radar" if radar else "manual"))
    for index, level in enumerate(("POOL", "L1", "L2", "L3")):
        await db.execute(insert(tables[Profile]).values(
            id=profiles[index], user_id=user, name=level, is_active=True))
        await db.execute(insert(tables[PipelineWatchlist]).values(
            id=watchlists[index], user_id=user, name=level, level=level,
            market_mode="spot", profile_id=profiles[index], auto_refresh=True,
            source_pool_id=pool if index == 0 else None,
            source_watchlist_id=watchlists[index - 1] if index else None))
        await db.execute(insert(tables[PipelineWatchlistAsset]).values(
            id=uuid4(), watchlist_id=watchlists[index], symbol="TEST_USDT", level_direction=None))
    await db.execute(insert(tables[DecisionLog]).values(
        id=1, user_id=user, profile_id=profiles[-1], symbol="TEST_USDT", created_at=now))
    await db.execute(insert(tables[ShadowTrade]).values(
        id=uuid4(), user_id=user, profile_id=profiles[-1], symbol="TEST_USDT",
        direction="SPOT", source="L3", status="RUNNING", decision_id=1, created_at=now))
    return user, pool, coin, watchlists


@pytest.mark.asyncio
async def test_absent_held_is_collected_but_hidden_and_return_restores_every_layer(postgres):
    sessions, tables = postgres
    async with sessions() as db, db.begin():
        user, pool, coin, watchlists = await seed_chain(db, tables)
        async def membership():
            return await load_radar_watchlist_eligibility(db, user_id=user, watchlist_ids=watchlists)
        assert await membership() == {wid: {"TEST_USDT"} for wid in watchlists}
        await db.execute(update(tables[PoolCoin]).where(tables[PoolCoin].c.id == coin)
                         .values(held_for_open_position=True))
        assert await membership() == {wid: set() for wid in watchlists}
        assert "TEST_USDT" in await get_active_pool_symbols(db, "spot")
        for wid in watchlists:
            wl = await db.get(PipelineWatchlist, wid)
            assets = (await db.execute(select(PipelineWatchlistAsset).where(
                PipelineWatchlistAsset.watchlist_id == wid))).scalars().all()
            assert await _intersect_assets_with_active_parent(wl, assets, db) == []
        await db.execute(update(tables[PoolCoin]).where(tables[PoolCoin].c.id == coin)
                         .values(held_for_open_position=False))
        assert await membership() == {wid: {"TEST_USDT"} for wid in watchlists}
        assert await db.scalar(select(ShadowTrade.status)) == "RUNNING"


@pytest.mark.asyncio
@pytest.mark.parametrize("health", [None, {"status": "unavailable"}])
async def test_missing_or_unavailable_feed_blocks_candidates_but_preserves_collection(postgres, health):
    sessions, tables = postgres
    async with sessions() as db, db.begin():
        user, pool, coin, watchlists = await seed_chain(db, tables)
        await db.execute(update(tables[Pool]).where(tables[Pool].c.id == pool).values(
            overrides={"radar_enabled": True, "radar_feed_health": health}))
        assert await load_radar_watchlist_eligibility(
            db, user_id=user, watchlist_ids=watchlists) == {wid: set() for wid in watchlists}
        assert "TEST_USDT" in await get_active_pool_symbols(db, "spot")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["origin", "user", "coin_market", "pool_market"])
async def test_membership_never_borrows_origin_tenant_or_market(postgres, invalid):
    sessions, tables = postgres
    async with sessions() as db, db.begin():
        user, pool, coin, watchlists = await seed_chain(db, tables)
        if invalid == "origin":
            await db.execute(update(tables[PoolCoin]).values(origin="discovered"))
        elif invalid == "user":
            await db.execute(update(tables[Pool]).values(user_id=uuid4()))
        elif invalid == "coin_market":
            await db.execute(update(tables[PoolCoin]).values(market_type="futures"))
        else:
            await db.execute(update(tables[Pool]).values(market_type="futures"))
        assert await load_radar_watchlist_eligibility(
            db, user_id=user, watchlist_ids=watchlists) == {wid: set() for wid in watchlists}


@pytest.mark.asyncio
async def test_nonradar_pool_keeps_existing_membership_policy(postgres):
    sessions, tables = postgres
    async with sessions() as db, db.begin():
        user, pool, coin, watchlists = await seed_chain(db, tables, radar=False)
        await db.execute(update(tables[PoolCoin]).values(held_for_open_position=True))
        assert await load_radar_watchlist_eligibility(db, user_id=user, watchlist_ids=watchlists) == {}
        assert await radar_shadow_entry_is_eligible(
            db, user_id=user, watchlist_id=watchlists[-1], symbol="TEST_USDT")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["parent_user", "parent_market", "missing_parent", "cycle"])
async def test_broken_or_foreign_parent_does_not_become_ungated_standalone(postgres, invalid):
    sessions, tables = postgres
    async with sessions() as db, db.begin():
        user, pool, coin, watchlists = await seed_chain(db, tables)
        parent = tables[PipelineWatchlist]
        if invalid == "parent_user":
            await db.execute(update(parent).where(parent.c.id == watchlists[-2]).values(user_id=uuid4()))
        elif invalid == "parent_market":
            await db.execute(update(parent).where(parent.c.id == watchlists[-2]).values(market_mode="futures"))
        elif invalid == "missing_parent":
            await db.execute(update(parent).where(parent.c.id == watchlists[-1]).values(source_watchlist_id=uuid4()))
        else:
            await db.execute(update(parent).where(parent.c.id == watchlists[1]).values(source_watchlist_id=watchlists[-2]))
        assert await load_radar_watchlist_eligibility(
            db, user_id=user, watchlist_ids=[watchlists[-1]]) == {watchlists[-1]: set()}
        assert not await radar_shadow_entry_is_eligible(
            db, user_id=user, watchlist_id=watchlists[-1], symbol="TEST_USDT")


@pytest.mark.asyncio
async def test_reappearing_radar_candidate_can_be_rejected_with_shadow_still_open(postgres, monkeypatch):
    from app.services import l3_public_authorization
    async def blocked(*args, **kwargs):
        return {}
    monkeypatch.setattr(l3_public_authorization, "load_public_authorizations", blocked)
    sessions, tables = postgres
    async with sessions() as db, db.begin():
        user, pool, coin, watchlists = await seed_chain(db, tables)
        rows = await load_live_l3_rejections(db, user_id=user, l3_watchlist_id=watchlists[-1])
        assert [row["symbol"] for row in rows] == ["TEST_USDT"]
        await db.execute(update(tables[PoolCoin]).values(held_for_open_position=True))
        assert await load_live_l3_rejections(db, user_id=user, l3_watchlist_id=watchlists[-1]) == []


@pytest.mark.asyncio
async def test_recent_shadow_floor_cannot_resurrect_held_or_unhealthy_radar(postgres, monkeypatch):
    from app.services import l3_public_authorization
    def allowed(*args, **kwargs):
        return {"alpha_score": 1, "current_price": 1, "evaluated_at": "now", "executable": True}
    monkeypatch.setattr(l3_public_authorization, "public_authorization", allowed)
    sessions, tables = postgres
    async with sessions() as db, db.begin():
        user, pool, coin, watchlists = await seed_chain(db, tables)
        async def recent():
            return await load_recently_authorized_l3_shadows(db, user_id=user, floor_seconds=300)
        assert [row.symbol for row in await recent()] == ["TEST_USDT"]
        await db.execute(update(tables[PoolCoin]).values(held_for_open_position=True))
        assert await recent() == []
        await db.execute(update(tables[PoolCoin]).values(held_for_open_position=False))
        await db.execute(update(tables[Pool]).values(overrides={"radar_enabled": True}))
        assert await recent() == []


@pytest.mark.asyncio
async def test_shadow_entry_waits_for_sync_and_rechecks_committed_feed_health(postgres):
    sessions, tables = postgres
    async with sessions() as db, db.begin():
        user, pool, coin, watchlists = await seed_chain(db, tables)
    async with sessions() as sync_db, sessions() as entry_db:
        await sync_db.execute(select(Pool.id).where(Pool.id == pool).with_for_update())
        await sync_db.execute(update(tables[Pool]).where(tables[Pool].c.id == pool).values(
            overrides={"radar_enabled": True, "radar_feed_health": {"status": "unavailable"}}))
        task = asyncio.create_task(radar_shadow_entry_is_eligible(
            entry_db, user_id=user, watchlist_id=watchlists[-1], symbol="TEST_USDT"))
        try:
            await asyncio.sleep(0.1)
            assert not task.done()
            await sync_db.commit()
            assert await asyncio.wait_for(task, timeout=5) is False
        finally:
            await sync_db.rollback()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await entry_db.rollback()
