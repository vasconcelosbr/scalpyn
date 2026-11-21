"""Admission/rollover tested against PostgreSQL and the actual Shadow writer."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.services import shadow_approved_source as service
from app.services import shadow_trade_service as writer
from app.services.shadow_entry_quote import require_current_quote as real_quote_guard
from app.models.config_profile import ConfigProfile
from app.models.profile import Profile
from app.models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from app.models.shadow_trade import ShadowTrade
from app.models.backoffice import DecisionLog, L3AuthorizationOutbox
from app.api.shadow_trades import _build_filters, _source_values, _to_read, _to_detail


class Adapter:
    def __init__(self, db): self.db = db
    async def execute(self, *args, **kwargs): return self.db.execute(*args, **kwargs)
    async def get(self, *args, **kwargs): return self.db.get(*args, **kwargs)
    @asynccontextmanager
    async def begin(self):
        try:
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
    async def scalar(self, *args, **kwargs): return self.db.scalar(*args, **kwargs)
    @asynccontextmanager
    async def begin_nested(self):
        with self.db.begin_nested(): yield


@pytest.fixture
def store():
    url = os.environ.get("MTF_TEST_DATABASE_URL")
    if not url: pytest.skip("MTF_TEST_DATABASE_URL is required")
    assert "mtf_chart_test" in url
    engine = sa.create_engine(url)
    schema = "shadow_source_" + uuid4().hex
    with engine.begin() as db:
        db.execute(sa.text(f"CREATE SCHEMA {schema}"))
        db.execute(sa.text(f"SET LOCAL search_path TO {schema},public"))
        # Mirror ORM columns, excluding unrelated FK dependencies. The real
        # source migration supplies the uniqueness constraints under test.
        metadata = sa.MetaData()
        for model in (ConfigProfile, Profile, PipelineWatchlist, PipelineWatchlistAsset, ShadowTrade, DecisionLog, L3AuthorizationOutbox):
            table = model.__table__.to_metadata(metadata)
            for col in table.c:
                if not col.primary_key: col.nullable = True
            if "created_at" in table.c:
                table.c.created_at.server_default = sa.DefaultClause(sa.text("now()"))
            db.execute(sa.schema.CreateTable(table, include_foreign_key_constraints=[]))
        db.execute(sa.text("CREATE TABLE profile_versions(id uuid, profile_id uuid, score_engine_version_id uuid, config_hash text, status text, version_number int)"))
        db.execute(sa.text("CREATE TABLE score_engine_versions(id uuid, config_hash text)"))
        path = Path(__file__).parents[1]/"alembic/versions/249_shadow_consolidated_armed.py"
        spec = importlib.util.spec_from_file_location("source_migration", path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade(); migration.upgrade()  # replay-safe
    def session():
        db = Session(engine)
        db.execute(sa.text(f"SET search_path TO {schema},public"))
        return db
    yield session
    with engine.begin() as db:
        db.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
    engine.dispose()


def seed(store):
    uid, pid, wid, aid, parent_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    with store() as db:
        db.add(ConfigProfile(user_id=uid, config_type="spot_engine", is_active=True,
            config_json={"shadow":{"approved_source":service.CONSOLIDATED}}))
        db.add(Profile(id=pid,user_id=uid,name="Reference",is_active=True,profile_version=now,
            config={"entry_triggers":{"conditions":[]}}))
        db.add(PipelineWatchlist(id=parent_id,user_id=uid,name="Parent",level="L2",market_mode="spot"))
        db.add(PipelineWatchlist(id=wid,user_id=uid,name="Original",level="L3",market_mode="spot",profile_id=pid,source_watchlist_id=parent_id))
        db.add(PipelineWatchlistAsset(id=aid,watchlist_id=parent_id,symbol="BTC_USDT",analysis_snapshot={"indicators":[{"key":"rsi","value":50}]}))
        db.commit()
    item = dict(symbol="BTC_USDT", profile_id=str(pid),watchlist_id=str(wid),asset_id=str(aid),
        candidate_profile_ids=[str(pid)],candidate_profile_names=["Reference"],decision_id=123,
        mtf={"status":"ARMED","observation_id":str(uuid4()),"valid_until":(now+timedelta(minutes=5)).isoformat()})
    return uid, item


def config():
    return dict(tp_pct=2,sl_pct=1,amount_usdt=100,timeout_candles=60,ttt_enabled=False,
        ttt_tp_pct=1,ttt_timeout_minutes=30,trailing={},shadow_entry_max_lag_seconds=60,
        ml_fee_roundtrip_pct=.2)


def quote():
    now=datetime.now(timezone.utc).isoformat()
    return dict(value=100,entry_at=now,source_at=now,age_seconds=0,contract_version="test_quote")


@pytest.fixture
def market(monkeypatch):
    from app.services import shadow_entry_quote, shadow_l3_exit_service, pool_service
    monkeypatch.setattr(shadow_entry_quote,"capture_entry_quote",AsyncMock(side_effect=lambda **kw: quote()))
    monkeypatch.setattr(shadow_entry_quote,"require_current_quote",lambda *a,**kw:None)
    monkeypatch.setattr(shadow_l3_exit_service,"load_frozen_policy",AsyncMock(return_value={"config":{"mode":"LEGACY"}}))
    monkeypatch.setattr(pool_service,"radar_shadow_entry_is_eligible",AsyncMock(return_value=True))


@pytest.mark.parametrize('signal', ['ARMED', 'TRIGGERED'])
def test_capture_preserves_history_reentry_and_observation_identity(store, market, monkeypatch, signal):
    uid,item=seed(store)
    item['mtf']['status'] = signal
    current=AsyncMock(return_value=(item,{"status":signal,"execution_authority":False}))
    monkeypatch.setattr(service,"current_observation",current)
    async def capture():
        with store() as db:
            result=await service.capture_one(Adapter(db),user_id=uid,symbol=item['symbol'],
                observation_id=item['mtf']['observation_id'],user_config=config())
            db.commit();return result
    first=asyncio.run(capture());assert first
    assert asyncio.run(capture()) is None
    with store() as db:
        row=db.get(ShadowTrade,first)
        assert row.source==service.CONSOLIDATED and row.decision_id is None
        assert str(row.mtf_observation_id)==item['mtf']['observation_id']
        assert row.profile_name=='Reference' and row.watchlist_name=='Original'
        assert row.entry_price==100 and row.tp_price==102 and row.sl_price==99
        assert row.eligible_for_training is False
        assert row.config_snapshot['l3_consolidated']['execution_authority'] is False
        assert row.config_snapshot['l3_consolidated']['entry_signal'] == signal
        assert row.config_snapshot['l3_consolidated']['source_asset_watchlist_id'] == str(db.get(PipelineWatchlist,UUID(item['watchlist_id'])).source_watchlist_id)
        assert row.watchlist_id == UUID(item['watchlist_id'])
        assert row.config_snapshot['shadow_l3_exit_policy']['config']['mode']=='LEGACY'
        assert _to_read(row).source==service.CONSOLIDATED
        assert _to_read(row).entry_signal == _to_detail(row).entry_signal == signal
        assert _to_detail(row).mtf_observation_id==row.mtf_observation_id
        assert asyncio.run(writer.record_as_simulation(Adapter(db),row)) is None
        row.status='COMPLETED';db.commit()
    assert asyncio.run(capture()) is None  # terminal rows still consume observation
    item['mtf']['observation_id']=str(uuid4())
    second=asyncio.run(capture());assert second and second!=first
    with store() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(ShadowTrade))==2
        assert db.get(ShadowTrade,first).status=='COMPLETED'


@pytest.mark.parametrize('mismatch', ['parent', 'symbol', 'owner'])
def test_capture_rejects_unrelated_parent_assets(store, market, monkeypatch, mismatch):
    uid,item=seed(store)
    monkeypatch.setattr(service, 'current_observation', AsyncMock(return_value=(item, {'status':'ARMED'})))
    with store() as db:
        asset=db.get(PipelineWatchlistAsset, UUID(item['asset_id']))
        if mismatch=='parent': asset.watchlist_id=UUID(item['watchlist_id'])
        elif mismatch=='symbol': asset.symbol='OTHER_USDT'
        else: db.get(PipelineWatchlist, asset.watchlist_id).user_id=uuid4()
        db.commit()
        assert asyncio.run(service.capture_one(Adapter(db), user_id=uid, symbol=item['symbol'],
            observation_id=item['mtf']['observation_id'], user_config=config())) is None
        assert db.scalar(sa.select(sa.func.count()).select_from(ShadowTrade))==0


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['ARMED','TRIGGERED','WAIT','NO_TRADE',None])
async def test_current_observation_accepts_only_armed_and_triggered(monkeypatch,state):
    uid,oid=uuid4(),uuid4()
    item=dict(symbol='BTC_USDT',mtf=dict(status=state,observation_id=str(oid),
        valid_until=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()))
    monkeypatch.setattr(service,'load_l3_armed_display',AsyncMock(return_value={
        'items':[item], 'analyses':{'BTC_USDT':{'status':state}}}))
    if state in ('ARMED','TRIGGERED'):
        assert (await service.current_observation(None,user_id=uid,symbol='BTC_USDT',observation_id=oid))[0]==item
    else:
        with pytest.raises(ValueError,match='SHADOW_SIGNAL_OBSERVATION_NO_LONGER_CURRENT'):
            await service.current_observation(None,user_id=uid,symbol='BTC_USDT',observation_id=oid)


def test_entry_signal_uses_frozen_origin_without_inventing_legacy_labels():
    from app.api.shadow_trades import _entry_signal
    row=SimpleNamespace(source='L3_CONSOLIDATED', config_snapshot={'l3_consolidated':{
        'entry_signal':'ARMED', 'mtf':{'status':'TRIGGERED'}}})
    assert _entry_signal(row)=='ARMED'
    row.config_snapshot={'l3_consolidated':{'mtf':{'status':'TRIGGERED'}}}
    assert _entry_signal(row)=='TRIGGERED'
    row.source='L3'
    assert _entry_signal(row) is None
    row.source='L3_CONSOLIDATED';row.config_snapshot={}
    assert _entry_signal(row) is None


def test_concurrent_workers_create_only_one_shadow(store, market, monkeypatch):
    uid,item=seed(store)
    monkeypatch.setattr(service,'current_observation',AsyncMock(return_value=(item,{'status':'ARMED'})))
    def capture(_):
        with store() as db:
            result=asyncio.run(service.capture_one(Adapter(db),user_id=uid,symbol=item['symbol'],
                observation_id=item['mtf']['observation_id'],user_config=config()))
            db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(capture,range(2)))
    assert sum(value is not None for value in results)==1


def test_old_writers_disabled_and_existing_open_trade_blocks_new_source(store,market,monkeypatch):
    uid,item=seed(store)
    with store() as db:
        legacy=ShadowTrade(user_id=uid,symbol='BTC_USDT',direction='SPOT',source='L3',status='RUNNING',amount_usdt=100)
        db.add(legacy);db.commit()
        assert not asyncio.run(service.admission(Adapter(db),user_id=uid,symbol='BTC_USDT',direction='SPOT',source='L3'))
        assert not asyncio.run(service.admission(Adapter(db),user_id=uid,symbol='BTC_USDT',direction='SPOT',source=service.CONSOLIDATED))
        decision=writer._SyntheticDecision(user_id=uid,symbol='OTHER_USDT')
        assert asyncio.run(writer._create_from_decision(Adapter(db),decision,'NOT_TRADABLE',{})) is None
        assert db.get(ShadowTrade,legacy.id).status=='RUNNING'
        assert asyncio.run(service.admission(Adapter(db),user_id=uid,symbol='BTC_USDT',direction='SPOT',source='L3_REJECTED'))


def test_observation_losing_eligibility_during_quote_cannot_insert(store,market,monkeypatch):
    uid,item=seed(store)
    monkeypatch.setattr(service,'current_observation',AsyncMock(side_effect=[(item,{}),ValueError('NO_LONGER_CURRENT')]))
    with store() as db:
        with pytest.raises(ValueError,match='NO_LONGER_CURRENT'):
            asyncio.run(service.capture_one(Adapter(db),user_id=uid,symbol=item['symbol'],
                observation_id=item['mtf']['observation_id'],user_config=config()))
        assert db.scalar(sa.select(sa.func.count()).select_from(ShadowTrade))==0


def test_quote_expiring_during_final_revalidation_cannot_insert(store,market,monkeypatch):
    from app.services import shadow_entry_quote
    # Restore the real age guard replaced by the generic market fixture.
    monkeypatch.setattr(shadow_entry_quote,'require_current_quote',real_quote_guard)
    frozen=quote()
    original=dict(frozen)
    class Clock(datetime):
        offset=timedelta()
        @classmethod
        def now(cls,tz=None): return datetime.now(tz)+cls.offset
    monkeypatch.setattr(writer,'datetime',Clock)
    monkeypatch.setattr(shadow_entry_quote,'capture_entry_quote',AsyncMock(return_value=frozen))
    uid,item=seed(store)
    calls=0
    async def revalidate(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:
            # Simulate elapsed DB work without sleeping or changing the configured limit.
            Clock.offset=timedelta(seconds=61)
        return item,{}
    monkeypatch.setattr(service,'current_observation',revalidate)
    with store() as db:
        with pytest.raises(shadow_entry_quote.EntryQuoteUnavailable,match='EXPIRED_BEFORE_INSERT'):
            asyncio.run(service.capture_one(Adapter(db),user_id=uid,symbol=item['symbol'],
                observation_id=item['mtf']['observation_id'],user_config=config()))
        assert calls==2
        assert frozen==original
        assert db.scalar(sa.select(sa.func.count()).select_from(ShadowTrade))==0


def test_multiple_sources_keep_profile_and_tenant_filters():
    uid,pid=uuid4(),uuid4()
    filters=_build_filters(user_id=uid,status=None,symbol=None,min_date=None,max_date=None,
        sources='L3,L3_CONSOLIDATED',profile_id=pid)
    sql=str(sa.select(ShadowTrade.id).where(*filters).compile(compile_kwargs={'literal_binds':True}))
    assert 'L3_CONSOLIDATED' in sql and uid.hex in sql and pid.hex in sql
    assert _source_values('L3')==['L3']
    from fastapi import HTTPException
    with pytest.raises(HTTPException): _source_values('L3','L3_CONSOLIDATED')
    with pytest.raises(HTTPException): _source_values(None,'BAD_SOURCE')


def test_replaced_outbox_event_is_terminal_without_shadow_retry(store, monkeypatch):
    from app import database
    from app.services import l3_authorization_outbox_service as outbox
    uid, _ = seed(store)
    with store() as db:
        decision = DecisionLog(user_id=uid, symbol="BTC_USDT", decision="ALLOW")
        db.add(decision); db.flush()
        event = L3AuthorizationOutbox(decision_id=decision.id, authorization_contract_hash="test",
            event_type=outbox.DIRECT_EVENT, status="RETRY", last_error="old error", payload={})
        db.add(event); db.commit(); event_id=event.id
    @asynccontextmanager
    async def session():
        with store() as db: yield Adapter(db)
    monkeypatch.setattr(database, "CeleryAsyncSessionLocal", session)
    assert asyncio.run(outbox._process_direct(event_id)) == service.SOURCE_REPLACED
    assert asyncio.run(outbox._process_direct(event_id)) == "SKIPPED"
    with store() as db:
        event=db.get(L3AuthorizationOutbox,event_id)
        assert event.status == "PROCESSED" and event.processed_at and event.last_error is None
        assert event.payload["processing_result"] == service.SOURCE_REPLACED
        assert db.scalar(sa.select(sa.func.count()).select_from(ShadowTrade)) == 0


def test_source_choice_is_gui_schema_validated():
    from app.schemas.spot_engine_config import ShadowRuntimeConfig
    from pydantic import ValidationError
    assert ShadowRuntimeConfig().approved_source == "L3"
    assert ShadowRuntimeConfig(approved_source="L3_CONSOLIDATED").model_dump()["approved_source"] == "L3_CONSOLIDATED"
    with pytest.raises(ValidationError): ShadowRuntimeConfig(approved_source="M5")
