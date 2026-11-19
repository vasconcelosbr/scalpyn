"""Frozen signal filtering must scope list, pagination and summary consistently."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from app.api import shadow_trades as api
from app.models.shadow_trade import ShadowTrade
from test_shadow_consolidated_armed import store, Adapter


def test_signal_filter_validates_and_preserves_tenant_scope():
    uid=uuid4()
    args=dict(user_id=uid,status=None,symbol=None,min_date=None,max_date=None)
    with pytest.raises(HTTPException) as exc:
        api._build_filters(**args,entry_signal="WAIT")
    assert exc.value.status_code==422
    from sqlalchemy.dialects import postgresql
    sql=str(sa.select(ShadowTrade.id).where(*api._build_filters(**args,entry_signal="ARMED")).compile(
        dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
    assert str(uid) in sql and "L3_CONSOLIDATED" in sql and "ARMED" in sql


def test_list_pagination_summary_and_frozen_label_agree(store, monkeypatch):
    uid,other,pid=uuid4(),uuid4(),uuid4()
    def row(signal=None,mtf=None,source="L3_CONSOLIDATED",user=uid):
        return ShadowTrade(user_id=user,profile_id=pid,symbol="BTC_USDT",source=source,status="RUNNING",
            amount_usdt=100,created_at=datetime.now(timezone.utc),
            config_snapshot={"l3_consolidated":{"entry_signal":signal,"mtf":{"status":mtf}}})
    with store() as db:
        rows=[row("ARMED","TRIGGERED"),row(None,"ARMED"),row("TRIGGERED","ARMED"),
              row("","TRIGGERED"),row(),row("WAIT","ARMED"),row("ARMED",source="L3"),row("ARMED",user=other)]
        db.add_all(rows);db.commit()
        monkeypatch.setattr(api,"_fetch_latest_prices",AsyncMock(return_value={}))
        monkeypatch.setattr(api,"latest_measurement_by_trade_ids",AsyncMock(return_value={}))
        monkeypatch.setattr(api,"_active_projection_overrides",AsyncMock(return_value={}))
        from app.services import shadow_l3_exit_service,shadow_trailing_view
        monkeypatch.setattr(shadow_l3_exit_service,"attach_states",AsyncMock())
        monkeypatch.setattr(shadow_trailing_view,"attach_trailing_views",AsyncMock())
        args=dict(db=Adapter(db),user_id=uid,status=None,symbol="BTC_USDT",min_date=None,max_date=None,
                  source=None,sources="L3,L3_CONSOLIDATED",profile_id=pid,profile_version=None)
        for signal in (None,"ARMED","TRIGGERED"):
            expected=[r for r in rows if r.user_id==uid and (signal is None or api._entry_signal(r)==signal)]
            first=asyncio.run(api.list_shadow_trades(**args,entry_signal=signal,page=1,page_size=1))
            second=asyncio.run(api.list_shadow_trades(**args,entry_signal=signal,page=2,page_size=1))
            summary=asyncio.run(api.shadow_trades_summary(**args,entry_signal=signal))
            assert first.total==second.total==summary.total==len(expected)
            assert first.items[0].id!=second.items[0].id
            if signal:
                assert first.items[0].entry_signal==second.items[0].entry_signal==signal
        rejected={**args,"source":"L3_REJECTED","sources":None}
        assert asyncio.run(api.list_shadow_trades(**rejected,entry_signal="ARMED",page=1,page_size=1)).total==0
        assert asyncio.run(api.shadow_trades_summary(**rejected,entry_signal="ARMED")).total==0


def test_invalid_signal_returns_validation_error_through_real_db_dependency(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import database
    @asynccontextmanager
    async def session():
        yield SimpleNamespace(rollback=AsyncMock(),in_transaction=lambda:False)
    monkeypatch.setattr(database,"AsyncSessionLocal",session)
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[api.get_current_user_id]=lambda:uuid4()
    with TestClient(app) as client:
        for path in ("/api/shadow-trades","/api/shadow-trades/summary"):
            response=client.get(path,params={"entry_signal":"INVALID"})
            assert response.status_code==422
            assert response.json()["detail"]=="entry_signal must be ARMED or TRIGGERED"
