import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import mtf_btc_market as market

AT = 1795129200 + 17

def ticker(price="110", at=AT):
    return {"price": price, "time": datetime.fromtimestamp(at, timezone.utc).isoformat()}

def candles():
    return [[(AT - seconds) // 60 * 60 - 60, 1000, close, 200, 1, 100, 10, "true"]
            for seconds, close in zip(market.WINDOWS.values(), (100, 120, 110))]


def test_rolling_returns_use_prior_close_and_independent_signs():
    rows = candles()
    # The next candle is not a known close at the requested historical boundary.
    rows.append([rows[0][0] + 60, 1000, 999, 1000, 1, 100, 10, "true"])
    result = market.snapshot(ticker(), list(reversed(rows)), now=AT+1, max_age_seconds=30)
    assert result["status"] == "READY" and result["currency"] == "USDT"
    assert result["changes"]["4h"]["pct"] == pytest.approx(10)
    assert result["changes"]["1h"]["pct"] == pytest.approx((110/120-1)*100)
    assert result["changes"]["5m"]["pct"] == 0
    for tf, seconds in market.WINDOWS.items():
        ref = datetime.fromisoformat(result["changes"][tf]["reference_at"]).timestamp()
        assert 0 <= AT-seconds-ref < 60


def test_missing_or_conflicting_reference_does_not_invent_zero_or_use_older_data():
    rows = candles()[1:]
    rows.append([candles()[0][0]-60, 1000, 100, 200, 1, 100, 10, "true"])
    rows.append([rows[0][0], 1000, 999, 1000, 1, 100, 10, "true"])
    result = market.snapshot(ticker(), rows, now=AT, max_age_seconds=30)
    assert result["status"] == "PARTIAL"
    assert result["changes"]["4h"]["pct"] is None
    assert result["changes"]["1h"]["pct"] is None
    assert result["changes"]["5m"]["pct"] == 0


@pytest.mark.parametrize("quote,status", [
    (ticker(at=AT-31), "STALE"), (ticker(at=AT+1), "UNAVAILABLE"),
    (ticker("NaN"), "UNAVAILABLE"), (ticker("-1"), "UNAVAILABLE"),
    ({"price":"110","time":"invalid"}, "UNAVAILABLE"), (None, "UNAVAILABLE"),
])
def test_unusable_quote_has_no_price_or_returns(quote, status):
    result = market.snapshot(quote, candles(), now=AT, max_age_seconds=30)
    assert result["status"] == status and result["price"] is None
    assert all(v["pct"] is None for v in result["changes"].values())


@pytest.mark.asyncio
async def test_quote_fetch_is_shared_and_cached_age_is_rechecked(monkeypatch):
    monkeypatch.setattr(market, "_cache", None)
    monkeypatch.setattr(market, "_lock", asyncio.Lock())
    fetch = AsyncMock(return_value=(ticker(),candles()))
    monkeypatch.setattr(market, "_fetch", fetch)
    monkeypatch.setattr(market.time, "time", lambda: AT)
    cfg = SimpleNamespace(poll_seconds=15, stale_tf_multiplier=2)
    a,b = await asyncio.gather(market.get_snapshot(cfg), market.get_snapshot(cfg))
    assert a == b and a["status"] == "READY"
    fetch.assert_awaited_once()
    monkeypatch.setattr(market.time, "time", lambda: AT+31)
    assert (await market.get_snapshot(cfg))["status"] == "STALE"
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_failure_keeps_only_the_available_quote(monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self, url, **kwargs):
            assert url.startswith("https://api.gateio.ws/api/v4/spot/")
            assert kwargs["params"]["currency_pair"] == "BTC_USDT"
            if url.endswith("candlesticks"): raise httpx.ReadTimeout("fixture")
            return httpx.Response(200, json=[{"currency_pair":"BTC_USDT","last":"110"}], request=httpx.Request("GET",url))
    monkeypatch.setattr(market.httpx,"AsyncClient",lambda **kw:Client())
    quote,rows = await market._fetch()
    result = market.snapshot(quote, rows, now=market.time.time(), max_age_seconds=30)
    assert result["price"] == 110 and result["status"] == "PARTIAL"


def test_authenticated_endpoint_releases_db_without_evaluating_or_writing(monkeypatch):
    from app.api import mtf_chart as api
    app = FastAPI(); app.include_router(api.router)
    with TestClient(app) as client:
        assert client.get("/api/mtf/btc-market").status_code in (401,403)
    db = SimpleNamespace(rollback=AsyncMock())
    app.dependency_overrides[api.get_current_user_id] = lambda: uuid4()
    app.dependency_overrides[api.get_db] = lambda: db
    monkeypatch.setattr(api, "config_or_error", AsyncMock(return_value=SimpleNamespace(poll_seconds=15,stale_tf_multiplier=2)))
    mock = AsyncMock(return_value=market.snapshot(ticker(),candles(),now=AT,max_age_seconds=30))
    monkeypatch.setattr(market, "get_snapshot", mock)
    monkeypatch.setattr(api.service, "evaluate", lambda *a,**kw:pytest.fail("display evaluated strategy"))
    write = AsyncMock(side_effect=AssertionError("display wrote configuration"))
    monkeypatch.setattr(api.config_service, "update_config", write)
    with TestClient(app) as client:
        response = client.get("/api/mtf/btc-market")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "READY"
    db.rollback.assert_awaited_once(); write.assert_not_awaited()


def test_unclosed_reference_is_not_used():
    rows=candles();rows[0][7]="false"
    result=market.snapshot(ticker(),rows,now=AT,max_age_seconds=30)
    assert result["changes"]["4h"]["pct"] is None
    assert result["source"]=="Gate.io" and result["symbol"]=="BTC_USDT"
