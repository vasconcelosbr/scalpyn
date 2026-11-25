import time
import asyncio
from uuid import uuid4
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import mtf_stream
from app.config import settings


@pytest.mark.parametrize('claims', [None, {'type':'refresh'}, {'type':'access','exp':1}])
def test_stream_rejects_invalid_expired_or_refresh_tokens_before_loading_data(monkeypatch, claims):
    async def forbidden(*args):
        pytest.fail('unauthorized seed access')
    monkeypatch.setattr(mtf_stream, 'authorized_seed', forbidden)
    app=FastAPI();app.include_router(mtf_stream.router)
    token='invalid' if claims is None else jwt.encode({'sub':str(uuid4()),'exp':time.time()+60,**claims},settings.JWT_SECRET,algorithm='HS256')
    with TestClient(app).websocket_connect('/api/mtf/stream') as ws:
        ws.send_json({'token':token})
        with pytest.raises(WebSocketDisconnect): ws.receive_json()


def test_membership_denial_closes_authenticated_stream_without_exchange_access(monkeypatch):
    uid=uuid4()
    async def denied(actual, request):
        assert actual==uid and request['watchlist_id']=='not-owned'
        raise ValueError('WATCHLIST_NOT_FOUND')
    monkeypatch.setattr(mtf_stream,'authorized_seed',denied)
    def forbidden(*args,**kwargs): pytest.fail('exchange connection before membership authorization')
    monkeypatch.setattr(mtf_stream.websockets,'connect',forbidden)
    app=FastAPI();app.include_router(mtf_stream.router)
    token=jwt.encode({'sub':str(uid),'exp':time.time()+60,'type':'access'},settings.JWT_SECRET,algorithm='HS256')
    with TestClient(app).websocket_connect('/api/mtf/stream') as ws:
        ws.send_json({'token':token,'symbol':'BTC_USDT','watchlist_id':'not-owned'})
        with pytest.raises(WebSocketDisconnect): ws.receive_json()


@pytest.mark.asyncio
async def test_seed_enforces_owned_membership_before_market_calculation(monkeypatch):
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
    monkeypatch.setattr(mtf_stream,'AsyncSessionLocal',Session)
    async def config(*args): return SimpleNamespace()
    async def members(*args): return []
    async def forbidden(*args): pytest.fail('chart before membership')
    monkeypatch.setattr(mtf_stream.chart,'load_config',config)
    monkeypatch.setattr(mtf_stream.chart,'display_members',members)
    monkeypatch.setattr(mtf_stream.chart,'chart_data',forbidden)
    with pytest.raises(ValueError,match='ASSET_NO_LONGER_IN_WATCHLIST'):
        await mtf_stream.authorized_seed(uuid4(),{'symbol':'BTC_USDT','watchlist_id':'other'})


def test_authenticated_stream_subscribes_and_cancels_upstream_on_browser_disconnect(monkeypatch):
    subscriptions=[]; closed=[]
    class Gate:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): closed.append(True)
        async def send(self,message): subscriptions.append(message)
        def __aiter__(self): return self.messages()
        async def messages(self):
            yield '{}'
            await asyncio.Event().wait()
    async def seed(*args):
        return SimpleNamespace(raw={'5m':[]},cfg=SimpleNamespace(poll_seconds=15),
                               update=lambda message,now: {'type':'preview','confirmation':'CLOSE_ONLY'})
    monkeypatch.setattr(mtf_stream,'authorized_seed',seed)
    monkeypatch.setattr(mtf_stream.websockets,'connect',lambda *args,**kwargs:Gate())
    app=FastAPI();app.include_router(mtf_stream.router)
    token=jwt.encode({'sub':str(uuid4()),'exp':time.time()+60,'type':'access'},settings.JWT_SECRET,algorithm='HS256')
    with TestClient(app) as client:
        with client.websocket_connect('/api/mtf/stream') as ws:
            ws.send_json({'token':token,'symbol':'BTC_USDT','watchlist_id':'owned'})
            assert ws.receive_json()=={'type':'preview','confirmation':'CLOSE_ONLY'}
    assert closed and len(subscriptions)==1
