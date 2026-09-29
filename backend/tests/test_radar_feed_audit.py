"""Receipt audit against an isolated SQLite DB; no production writes."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Column, JSON, MetaData, Table, select, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.pool import Pool, PoolCoin
from app.models.radar_feed_audit import RadarFeedReceipt, RadarFeedItem
from app.services.radar_feed_audit import record_receipt, complete_receipt, purge_expired, display_time, parse_source_updated_at, RETENTION
from app.api.pools import radar_history


@pytest_asyncio.fixture
async def audit_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    @event.listens_for(engine.sync_engine, "connect")
    def fk_on(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    metadata = MetaData()
    for model in (Pool, PoolCoin):
        Table(model.__tablename__, metadata, *(Column(c.name,
              JSON if isinstance(c.type, JSONB) else c.type,
              primary_key=c.primary_key) for c in model.__table__.columns))
    RadarFeedReceipt.__table__.to_metadata(metadata)
    RadarFeedItem.__table__.to_metadata(metadata)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user, pool_id, other = uuid4(), uuid4(), uuid4()
    async with sessions() as db, db.begin():
        db.add_all([Pool(id=pool_id, user_id=user, name="PUMP"),
                    Pool(id=other, user_id=uuid4(), name="OTHER")])
    yield sessions, user, pool_id, other
    await engine.dispose()


def test_exact_display_and_missing_source_timestamp():
    at = datetime(2026, 9, 29, 15, 30, tzinfo=timezone.utc)
    assert display_time(at) == "29/09/2026, 12:30:00 GMT-3"
    assert parse_source_updated_at("2026-09-29T15:30:00Z") == at
    for value in (None, "invalid", "2026-09-29T15:30:00", 1790681400):
        assert parse_source_updated_at(value) is None


@pytest.mark.asyncio
async def test_all_entries_survive_pool_removal_and_repeat_polls(audit_db):
    sessions, user, pool, other = audit_db
    now = datetime.now(timezone.utc)
    assets = [{"pair":"SUI_USDT","updated_at":now.isoformat()},
              {"pair":"SUI_USDT"}, {"pair":"BTC_EUR"}]
    async with sessions() as db, db.begin():
        receipt = await record_receipt(db, pool_id=pool, assets=assets, received_at=now,
                                      selected_pairs={"SUI_USDT"})
        db.add(PoolCoin(pool_id=pool,symbol="SUI_USDT",origin="radar",is_active=True,held_for_open_position=False))
        await db.flush()
        await complete_receipt(db,receipt,reconciled=True)
        coins = (await db.scalars(select(PoolCoin))).all()
        for coin in coins:
            await db.delete(coin)
        await record_receipt(db,pool_id=pool,assets=assets,received_at=now+timedelta(seconds=1),selected_pairs={"SUI_USDT"})
        await record_receipt(db,pool_id=other,assets=assets,received_at=now,selected_pairs=set())
    async with sessions() as db:
        result = await radar_history(pool,"",0,50,db,user)
        assert result["total"] == 6
        assert {i["pool_result"] for i in result["items"]} == {"PRESENT","FILTERED","PENDING"}
        assert sum(i["source_updated_at"] is None for i in result["items"]) == 4
        assert (await radar_history(pool,"SUI",0,1,db,user))["total"] == 4
        assert len((await radar_history(pool,"SUI",1,1,db,user))["items"]) == 1
        assert (await radar_history(pool,"%",0,50,db,user))["total"] == 0
        with pytest.raises(HTTPException) as error:
            await radar_history(other,"",0,50,db,user)
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_retention_hides_expired_then_deletes_parent_and_items(audit_db):
    sessions, user, pool, other = audit_db
    now = datetime.now(timezone.utc)
    async with sessions() as db, db.begin():
        for at in (now-RETENTION-timedelta(seconds=1),now-timedelta(days=6)):
            await record_receipt(db,pool_id=pool,assets=[{"pair":"NEAR_USDT"}],received_at=at,selected_pairs={"NEAR_USDT"})
        result=await radar_history(pool,"",0,50,db,user)
        assert result["total"] == 1
        assert await purge_expired(db,now) == 1
        assert len((await db.scalars(select(RadarFeedItem))).all()) == 1
        assert len((await db.scalars(select(RadarFeedReceipt))).all()) == 1


@pytest.mark.asyncio
async def test_empty_unavailable_and_failed_sync_are_distinct(audit_db):
    sessions, user, pool, other = audit_db
    now=datetime.now(timezone.utc)
    async with sessions() as db, db.begin():
        empty=await record_receipt(db,pool_id=pool,assets=[],received_at=now,selected_pairs=set())
        await complete_receipt(db,empty,reconciled=True)
        failure=await record_receipt(db,pool_id=pool,assets=None,received_at=now,selected_pairs=set(),reason="fetch_failed")
        incoming=await record_receipt(db,pool_id=pool,assets=[{"pair":"ADA_USDT"}],received_at=now,selected_pairs={"ADA_USDT"})
        await complete_receipt(db,incoming,reconciled=False)
        assert (await db.get(RadarFeedReceipt,empty)).status == "SYNCED"
        assert (await db.get(RadarFeedReceipt,failure)).status == "UNAVAILABLE"
        assert (await db.get(RadarFeedReceipt,incoming)).status == "SYNC_FAILED"
        assert (await db.scalars(select(RadarFeedItem))).one().pool_result == "NOT_VERIFIED"


@pytest.mark.asyncio
async def test_missing_membership_not_reported_as_approval(audit_db):
    sessions,user,pool,other=audit_db
    async with sessions() as db, db.begin():
        receipt=await record_receipt(db,pool_id=pool,assets=[{"pair":"ADA_USDT"}],
            received_at=datetime.now(timezone.utc),selected_pairs={"ADA_USDT"})
        await complete_receipt(db,receipt,reconciled=True)
        assert (await db.scalars(select(RadarFeedItem))).one().pool_result == "NOT_INCLUDED"


@pytest.mark.asyncio
async def test_audit_failure_cannot_stop_existing_reconciliation(monkeypatch):
    from app.tasks.radar_auto_discover import _radar_sync_async
    from app import database
    from app.services import radar_service
    calls=[]
    uid=uuid4()
    async def run(fn, **kwargs):
        calls.append(fn.__name__)
        if fn.__name__ == "_load_pools":
            return [{"id":uuid4(),"user_id":uid,"name":"PUMP"}]
        if fn.__name__ == "_load_key":
            return "test-key"
        if fn.__name__ == "_audit":
            raise RuntimeError("audit DB unavailable")
        if fn.__name__ == "_persist":
            return {"added":1,"removed":0,"held":0,"duplicates":0,"skipped":False}
        raise AssertionError(fn.__name__)
    monkeypatch.setattr(database,"run_db_task",run)
    monkeypatch.setattr(radar_service,"fetch_top_assets",AsyncMock(return_value=[{"pair":"SUI_USDT"}]))
    assert await _radar_sync_async() == "1 pools | +1 -0"
    assert calls.index("_audit") < calls.index("_persist")
