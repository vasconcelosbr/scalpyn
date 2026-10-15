"""Opt-in integration proof against a disposable LOCAL PostgreSQL database."""
import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services import radar_readonly as service


def test_postgres_readonly_snapshot_and_source_parity(monkeypatch):
    url = os.getenv("RADAR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RADAR_TEST_DATABASE_URL must point to disposable localhost PostgreSQL")
    from sqlalchemy.engine import make_url
    assert make_url(url).host in {"localhost", "127.0.0.1", "::1"}

    async def run():
        schema = "radar_test_" + uuid4().hex
        admin = create_async_engine(url, poolclass=NullPool)
        engine = create_async_engine(url, poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}})
        owner, wid, aid = uuid4(), uuid4(), uuid4()
        try:
            async with admin.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            async with engine.begin() as conn:
                for sql in [
                    "CREATE TABLE pipeline_watchlists (id uuid, user_id uuid, name text, level text, market_mode text, source_pool_id uuid, source_watchlist_id uuid, last_scanned_at timestamptz)",
                    "CREATE TABLE pipeline_watchlist_assets (id uuid, watchlist_id uuid, symbol text, current_price numeric, price_change_24h numeric, volume_24h numeric, market_cap numeric, alpha_score numeric, entered_at timestamptz, refreshed_at timestamptz, analysis_snapshot jsonb, level_direction text)",
                    "CREATE TABLE indicators (symbol text, market_type text, scheduler_group text, timeframe text, time timestamptz, indicators_json jsonb)",
                    "CREATE TABLE pools (id uuid,user_id uuid,name text,market_type text,is_active boolean,updated_at timestamptz)",
                    "CREATE TABLE pool_coins (id uuid,pool_id uuid,symbol text,market_type text,is_active boolean,is_approved boolean,is_tradable boolean,held_for_open_position boolean,added_at timestamptz,origin text,discovered_at timestamptz,radar_last_seen_at timestamptz)",
                ]:
                    await conn.execute(text(sql))
                await conn.execute(text("INSERT INTO pipeline_watchlists VALUES (:w, :u, 'POOL', 'POOL', 'spot', NULL, NULL, '2020-01-01')"), {"w": wid, "u": owner})
                await conn.execute(text("INSERT INTO pipeline_watchlist_assets VALUES (:a, :w, 'AKE_USDT', 0.0357, NULL, NULL, NULL, 75, '2020-01-01', '2020-01-01', '{\"original\": true}', NULL)"), {"a": aid, "w": wid})
                await conn.execute(text("INSERT INTO pipeline_watchlist_assets (id, watchlist_id, symbol, level_direction) VALUES (:a, :w, 'DOWN_USDT', 'down')"), {"a": uuid4(), "w": wid})
                await conn.execute(text("""INSERT INTO indicators VALUES
                    ('AKE_USDT','spot','structural','5m','2020-01-01','{"rsi": 41}'),
                    ('AKE_USDT','spot','structural','5m','2020-01-02','{"rsi": 42}'),
                    ('AKE_USDT','spot','microstructure','5m','2020-01-02','{"taker_ratio": 0.6}'),
                    ('AKE_USDT','spot','structural','1h','2020-01-02','{"rsi": 51}'),
                    ('AKE_USDT','futures','structural','5m','2020-01-03','{"rsi": 99}')"""))
                await conn.execute(text("INSERT INTO pools VALUES (:p,:u,'POOL','spot',true,NULL)"),{'p':wid,'u':owner})
                await conn.execute(text("UPDATE pipeline_watchlists SET source_pool_id=:p"),{'p':wid})
                for symbol,market,active in [('AKE_USDT','spot',True),('AKE_USDT','futures',True),('EMPTY_USDT','spot',True),('OLD_USDT','spot',False)]:
                    await conn.execute(text("INSERT INTO pool_coins(id,pool_id,symbol,market_type,is_active,held_for_open_position) VALUES (:id,:p,:s,:m,:a,false)"),{'id':uuid4(),'p':wid,'s':symbol,'m':market,'a':active})
            monkeypatch.setattr(service, "AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False))
            async with service.readonly_session() as db:
                assert (await db.execute(text("SHOW transaction_read_only"))).scalar() == "on"
                assert (await db.execute(text("SHOW transaction_isolation"))).scalar() == "repeatable read"
                result = await service.read_watchlist(db, wid, owner)
                assert result.asset_count == 1
                assert result.assets[0].current_price == 0.0357
                assert result.assets[0].analysis_snapshot == {"original": True}
                assert len(result.assets[0].indicator_readings) == 3
                assert {r.timeframe for r in result.assets[0].indicator_readings} == {"1h", "5m"}
                assert {r.values.get("rsi") for r in result.assets[0].indicator_readings} == {None, 42, 51}
                assert await service.read_watchlist(db, wid, uuid4()) is None
                from app.services.radar_pool_readonly import read_pool_context
                from app.schemas.radar_readonly import RadarPoolContext
                pool=RadarPoolContext.model_validate(await read_pool_context(db,wid,owner))
                assert pool.row_count==4 and pool.active_count==3
                futures=next(c for c in pool.coins if c.market_type=='futures')
                assert futures.indicator_readings[0].values=={'rsi':99}
                assert next(c for c in pool.coins if c.symbol=='EMPTY_USDT').indicator_readings==[]
                assert await read_pool_context(db,wid,uuid4()) is None
                async with engine.begin() as concurrent:
                    await concurrent.execute(text("UPDATE pipeline_watchlist_assets SET alpha_score=80"))
                same_snapshot = await service.read_watchlist(db, wid, owner)
                assert same_snapshot.assets[0].alpha_score == 75
            for statement in ["UPDATE pipeline_watchlist_assets SET alpha_score=0",
                              "INSERT INTO indicators(symbol) VALUES ('BAD')",
                              "DELETE FROM pipeline_watchlist_assets"]:
                with pytest.raises(DBAPIError) as error:
                    async with service.readonly_session() as db:
                        await db.execute(text(statement))
                assert error.value.orig.sqlstate == "25006"
            async with service.readonly_session() as db:
                assert (await service.read_watchlist(db, wid, owner)).assets[0].alpha_score == 80
        finally:
            await engine.dispose()
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())
