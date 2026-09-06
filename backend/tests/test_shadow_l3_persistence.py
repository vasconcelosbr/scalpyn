"""Real PostgreSQL integration. Requires SHADOW_L3_TEST_DATABASE_URL (local only)."""
import importlib.util
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.schema import CreateTable

from app.models.shadow_trade import ShadowTrade
from app.models.config_profile import ConfigProfile, ConfigAuditLog
from app.schemas.shadow_l3_exit_policy import frozen_policy
from app.services.shadow_l3_exit_service import advance_shadow, attach_states
from app.services.shadow_l3_flow_capture import drain
from app.services.config_service import ConfigService

URL=os.environ.get("SHADOW_L3_TEST_DATABASE_URL")
pytestmark=pytest.mark.skipif(not URL,reason="Local PostgreSQL URL required")


@pytest_asyncio.fixture
async def db():
    assert URL and ("127.0.0.1" in URL or "localhost" in URL), "Integration tests must use local PostgreSQL"
    schema="l3_test_"+uuid4().hex
    sync=create_engine(URL)
    with sync.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f'SET LOCAL search_path TO "{schema}",public'))
        c.execute(text("CREATE TABLE users(id UUID PRIMARY KEY)"))
        for table in (ShadowTrade.__table__,ConfigProfile.__table__,ConfigAuditLog.__table__):
            c.execute(CreateTable(table,include_foreign_key_constraints=[]))
        c.execute(text("CREATE TABLE ohlcv(time timestamptz,symbol text,exchange text,market_type text,timeframe text,open numeric,high numeric,low numeric,close numeric,is_closed boolean,ingested_at timestamptz)"))
        path=Path(__file__).parents[1]/"alembic/versions/219_shadow_l3_continuation.py"
        spec=importlib.util.spec_from_file_location("l3_migration",path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.op=Operations(MigrationContext.configure(c))
        module.upgrade();module.upgrade()
        with pytest.raises(RuntimeError,match="Forward-only"):
            module.downgrade()
    engine=create_async_engine(URL.replace("postgresql://","postgresql+asyncpg://"),connect_args={"server_settings":{"search_path":f'{schema},public'}})
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
    with sync.begin() as c: c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    sync.dispose()


@pytest.mark.asyncio
async def test_durable_capture_deduplicates_after_ack_failure(db):
    t=datetime.now(timezone.utc).isoformat()
    row=dict(exchange="gate.io",market_type="spot",symbol="UNI_USDT",trade_id="trade-a",occurred_at=t,available_at=t,side="buy",amount="4")
    class Redis:
        fail=True
        async def xrange(self,*args,**kw):return [(b"1-0",{b"payload":json.dumps(row).encode()})]
        async def xdel(self,*args):
            if self.fail: raise ConnectionError("ack failed")
    redis=Redis()
    with pytest.raises(ConnectionError): await drain(db,redis)
    redis.fail=False
    await drain(db,redis)
    result=(await db.execute(text("SELECT count(*),sum(amount) FROM shadow_l3_flow_trades"))).one()
    assert result[0]==1 and result[1]==4


@pytest.mark.asyncio
async def test_observation_restart_preserves_real_outcome_and_tenant(db):
    uid=uuid4()
    await db.execute(text("INSERT INTO users VALUES(:uid)"),{"uid":uid})
    entry=datetime(2026,1,1,tzinfo=timezone.utc)
    shadow=ShadowTrade(id=uuid4(),user_id=uid,symbol="UNI_USDT",source="L3",status="RUNNING",entry_price=100,
                       entry_timestamp=entry,tp_price=102,sl_price=95,amount_usdt=100,exchange="gate.io",
                       config_snapshot={"shadow_l3_exit_policy":frozen_policy({})},timeout_candles=60)
    db.add(shadow);await db.commit()
    await db.execute(text("INSERT INTO ohlcv VALUES(:at,'UNI_USDT','gate.io','spot','1m',100,103,100,102,true,:available)"),
                     {"at":entry,"available":entry})
    await db.commit()
    result=await advance_shadow(db,shadow);await db.commit()
    assert result["outcome"]=="TP_HIT"
    assert shadow.status=="RUNNING" and shadow.outcome is None
    assert await advance_shadow(db,shadow)==result
    assert (await db.execute(text("SELECT count(*) FROM shadow_l3_exit_decisions"))).scalar()==1
    await attach_states(db,[shadow],uuid4())
    assert shadow.l3_exit is None
    await attach_states(db,[shadow],uid)
    assert shadow.l3_exit["mode"]=="OBSERVE"
    await db.execute(text("INSERT INTO ohlcv VALUES(:at,'UNI_USDT','gate.io','spot','1m',103,105,102,104,true,:available)"),
                     {"at":entry+timedelta(minutes=1),"available":entry+timedelta(minutes=2)})
    await db.commit()
    later=await advance_shadow(db,shadow);await db.commit()
    assert later["exit_price"]==result["exit_price"]
    assert later["observation_cursor"]==(entry+timedelta(minutes=1)).isoformat()
    assert (await db.execute(text("SELECT count(*) FROM shadow_l3_exit_decisions"))).scalar()==2
    assert shadow.status=="RUNNING" and shadow.outcome is None


@pytest.mark.asyncio
async def test_global_policy_save_is_audited_and_application_blocked(db):
    uid=uuid4();await db.execute(text("INSERT INTO users VALUES(:uid)"),{"uid":uid});await db.commit()
    service=ConfigService();service.redis=None
    config=frozen_policy({})["config"]
    saved=await service.update_config(db,"shadow_l3_exit_policy",uid,config,uid)
    assert saved==await service.get_config(db,"shadow_l3_exit_policy",uid)
    assert (await db.execute(text("SELECT count(*) FROM config_audit_log"))).scalar()==1
    with pytest.raises(ValueError):await service.validate_shadow_l3_policy(db,{**config,"mode":"APPLY"},uid)
    with pytest.raises(ValueError):await service.validate_shadow_l3_policy(db,config,uid,uuid4())
    assert await service.get_config(db,"shadow_l3_exit_policy",uuid4())=={}


@pytest.mark.asyncio
async def test_concurrent_evaluation_is_serialized_and_idempotent(db):
    import asyncio
    uid=uuid4();await db.execute(text("INSERT INTO users VALUES(:uid)"),{"uid":uid})
    entry=datetime(2026,1,1,tzinfo=timezone.utc)
    sid=uuid4()
    shadow=ShadowTrade(id=sid,user_id=uid,symbol="UNI_USDT",source="L3",status="RUNNING",entry_price=100,
                       entry_timestamp=entry,tp_price=102,sl_price=95,amount_usdt=100,exchange="gate.io",
                       config_snapshot={"shadow_l3_exit_policy":frozen_policy({})},timeout_candles=60)
    db.add(shadow)
    await db.execute(text("INSERT INTO ohlcv VALUES(:at,'UNI_USDT','gate.io','spot','1m',100,103,100,102,true,:at)"),{"at":entry})
    await db.commit()
    factory=async_sessionmaker(db.bind,expire_on_commit=False)
    async def evaluate():
        async with factory() as session:
            trade=await session.get(ShadowTrade,sid)
            result=await advance_shadow(session,trade)
            await session.commit()
            return result
    a,b=await asyncio.gather(evaluate(),evaluate())
    assert a==b
    assert (await db.execute(text("SELECT count(*) FROM shadow_l3_exit_decisions"))).scalar()==1
    assert (await db.execute(text("SELECT count(*) FROM shadow_l3_exit_states"))).scalar()==1


@pytest.mark.asyncio
async def test_authorized_apply_roundtrip_and_real_shadow_finalization(db):
    from app.schemas.shadow_l3_exit_policy import ShadowL3ExitPolicy
    uid=uuid4();await db.execute(text("INSERT INTO users VALUES(:uid)"),{"uid":uid});await db.commit()
    values=json.loads((Path(__file__).parents[2]/"docs/shadow-l3-initial-policy.json").read_text())
    policy=ShadowL3ExitPolicy.model_validate(values)
    service=ConfigService();service.redis=None
    await service.update_config(db,"shadow_l3_exit_policy",uid,values,uid)
    fresh=await service.get_config(db,"shadow_l3_exit_policy",uid)
    assert fresh["mode"]=="APPLY" and ShadowL3ExitPolicy.model_validate(fresh).digest()==policy.digest()
    assert (await db.execute(text("SELECT count(*) FROM shadow_l3_policy_validations"))).scalar()==0
    entry=datetime(2026,1,1,tzinfo=timezone.utc)
    shadow=ShadowTrade(id=uuid4(),user_id=uid,symbol="UNI_USDT",source="L3",status="RUNNING",entry_price=100,
                       entry_timestamp=entry,tp_price=102,sl_price=95,amount_usdt=100,exchange="gate",
                       config_snapshot={"shadow_l3_exit_policy":frozen_policy(values)},timeout_candles=60,
                       eligible_for_training=True)
    db.add(shadow);await db.commit()
    await db.execute(text("INSERT INTO ohlcv VALUES(:at,'UNI_USDT','gate.io','spot','1m',100,103,100,102,true,:at)"),{"at":entry})
    await db.commit()
    state=await advance_shadow(db,shadow);await db.commit()
    assert state["outcome"]=="TP_HIT" and shadow.outcome=="TP_HIT"
    assert shadow.status=="COMPLETED" and shadow.exit_price==102
    assert shadow.eligible_for_training is False
    assert await advance_shadow(db,shadow)==state


@pytest.mark.asyncio
async def test_registration_is_idempotent_and_legacy_has_no_enrollment(db):
    from app.services.shadow_l3_exit_service import register_shadow
    uid=uuid4();await db.execute(text("INSERT INTO users VALUES(:uid)"),{"uid":uid})
    sid=uuid4();db.add(ShadowTrade(id=sid,user_id=uid,symbol="UNI_USDT",source="L3",status="PENDING",amount_usdt=100))
    await db.commit()
    await register_shadow(db,sid,uid,frozen_policy({"mode":"LEGACY"}))
    assert (await db.execute(text("SELECT count(*) FROM shadow_l3_exit_states"))).scalar()==0
    await register_shadow(db,sid,uid,frozen_policy({}))
    await register_shadow(db,sid,uid,frozen_policy({}))
    assert (await db.execute(text("SELECT count(*) FROM shadow_l3_exit_states"))).scalar()==1


@pytest.mark.asyncio
async def test_recovery_sweep_uses_canonical_gate_candles(db,monkeypatch):
    from app.tasks.shadow_l3_continuation import _sweep
    import app.database as database
    import app.services.redis_client as redis_client
    async def no_redis():return None
    monkeypatch.setattr(redis_client,"get_async_redis",no_redis)
    monkeypatch.setattr(database,"CeleryAsyncSessionLocal",async_sessionmaker(db.bind,expire_on_commit=False))
    uid=uuid4();await db.execute(text("INSERT INTO users VALUES(:uid)"),{"uid":uid})
    entry=datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=2)
    shadow=ShadowTrade(id=uuid4(),user_id=uid,symbol="UNI_USDT",source="L3",status="RUNNING",entry_price=100,
                       entry_timestamp=entry,tp_price=102,sl_price=95,amount_usdt=100,exchange="gate",
                       config_snapshot={"shadow_l3_exit_policy":frozen_policy({})},timeout_candles=60)
    db.add(shadow)
    await db.execute(text("INSERT INTO ohlcv VALUES(:at,'UNI_USDT','gate.io','spot','1m',100,103,100,102,true,:at)"),{"at":entry})
    await db.commit()
    result=await _sweep()
    assert result=={"processed":1,"errors":0}
    assert (await db.execute(text("SELECT state->>'outcome' FROM shadow_l3_exit_states"))).scalar()=="TP_HIT"
    assert (await db.execute(text("SELECT count(*) FROM shadow_l3_exit_decisions"))).scalar()==1
