"""Tests for the bearer-token gate on ``/api/admin/symbol-health/{symbol}``.

Mirrors the contract tested for ``/metrics`` in
``test_metrics_endpoint_auth.py``:

* ``ADMIN_DIAGNOSTICS_TOKEN`` unset            → 404 (endpoint hidden)
* env set, missing/wrong header                → 401 with WWW-Authenticate
* env set, correct bearer                      → 200 with JSON body

The 200-path test stubs every probe so the suite does not hit the real
DB, Redis, or Gate.io. The point is to lock the **shape** of the
response document — operators rely on the probe keys being stable.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api import admin_diagnostics as admin_api  # noqa: E402


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_api.router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ADMIN_DIAGNOSTICS_TOKEN", raising=False)
    yield


def test_returns_404_when_token_not_configured(client: TestClient) -> None:
    response = client.get("/api/admin/symbol-health/DOGE_USDT")
    assert response.status_code == 404


def test_returns_404_when_token_is_blank(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "   ")
    response = client.get("/api/admin/symbol-health/DOGE_USDT")
    assert response.status_code == 404


def test_returns_401_when_authorization_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    response = client.get("/api/admin/symbol-health/DOGE_USDT")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_returns_401_when_scheme_is_not_bearer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    response = client.get(
        "/api/admin/symbol-health/DOGE_USDT",
        headers={"Authorization": "Basic czNjcmV0"},
    )
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_returns_401_when_token_is_wrong(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    response = client.get(
        "/api/admin/symbol-health/DOGE_USDT",
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def _stub_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every probe with a deterministic fake to keep the test
    hermetic — no DB, no Redis, no exchange."""
    async def fake_pool(symbol):
        return {
            "ok": True,
            "found": True,
            "memberships": [
                {
                    "pool_coin_id": 1,
                    "pool_id": 7,
                    "pool_name": "L3-spot",
                    "market_type": "spot",
                    "is_active": True,
                    "is_approved": True,
                }
            ],
            "any_approved_active_spot": True,
        }

    async def fake_resolver(symbol):
        return {
            "ok": True,
            "in_ws_subscription": True,
            "in_microstructure_scheduler": True,
            "drift_for_this_symbol": False,
            "drift_reason": None,
            "totals": {
                "ws_universe_size": 42,
                "microstructure_universe_size": 42,
                "only_in_ws": 0,
                "only_in_microstructure": 0,
            },
        }

    async def fake_buffer(symbol):
        return {
            "ok": True,
            "redis_available": True,
            "key": f"trades_buffer:spot:{symbol}",
            "exists": True,
            "member_count": 17,
            "ttl_seconds": 300,
            "oldest_trade_age_seconds": 250.0,
            "newest_trade_age_seconds": 1.2,
        }

    async def fake_history(symbol):
        return {"ok": True, "rows": []}

    async def fake_ohlcv_hist(symbol):
        return {"ok": True, "by_timeframe": {"5m": {"present": False},
                                              "1h": {"present": False}}}

    async def fake_live_ob(symbol):
        return {"ok": True, "spread_pct": 0.01,
                "orderbook_depth_usdt": 100000.0,
                "source": "gate", "raw_keys": []}

    async def fake_live_ohlcv(symbol):
        return {"ok": True, "rows": 100,
                "exchange": "gate.io", "last_time": "2026-05-03T00:00:00+00:00"}

    async def fake_live_of(symbol):
        return {
            "ok": True,
            "taker_ratio": 0.55,
            "buy_pressure": 0.55,
            "volume_delta": 12.0,
            "taker_buy_volume": 100.0,
            "taker_sell_volume": 80.0,
            "source": "gate_trades_ws_spot",
            "window": "300s",
        }

    async def fake_leader():
        return {
            "ok": True,
            "redis_available": True,
            "leader_holder": "instance-A",
            "leader_ttl_seconds": 25,
            "elected": True,
        }

    monkeypatch.setattr(admin_api, "_probe_pool_status", fake_pool)
    monkeypatch.setattr(admin_api, "_probe_resolver_diff", fake_resolver)
    monkeypatch.setattr(admin_api, "_probe_trade_buffer", fake_buffer)
    monkeypatch.setattr(admin_api, "_probe_indicators_history", fake_history)
    monkeypatch.setattr(admin_api, "_probe_ohlcv_history", fake_ohlcv_hist)
    monkeypatch.setattr(admin_api, "_probe_live_orderbook", fake_live_ob)
    monkeypatch.setattr(admin_api, "_probe_live_ohlcv", fake_live_ohlcv)
    monkeypatch.setattr(admin_api, "_probe_live_order_flow", fake_live_of)
    monkeypatch.setattr(admin_api, "_probe_ws_leader_status", fake_leader)


def test_returns_200_with_correct_token_and_full_document_shape(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    _stub_probes(monkeypatch)

    response = client.get(
        "/api/admin/symbol-health/DOGE_USDT",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    body = response.json()

    # Top-level shape — locks the public contract operators rely on.
    assert body["symbol"] == "DOGE_USDT"
    assert "checked_at" in body
    for key in (
        "pool_status",
        "resolver_diff",
        "trade_buffer",
        "indicators_history",
        "ohlcv_history",
        "live_probes",
        "ws_leader_status",
    ):
        assert key in body, f"missing top-level key: {key}"

    # live_probes is a nested grouping — keep its three sub-probes pinned.
    for key in ("orderbook_metrics", "ohlcv_5m", "order_flow_300s"):
        assert key in body["live_probes"], f"missing live_probes.{key}"


def test_symbol_is_uppercased_before_probes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    _stub_probes(monkeypatch)

    response = client.get(
        "/api/admin/symbol-health/doge_usdt",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    assert response.json()["symbol"] == "DOGE_USDT"


def test_failed_probe_does_not_500_the_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagnostic that crashes its own subsystem must still return
    a complete document — that's the whole point of having it."""
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    _stub_probes(monkeypatch)

    async def boom(symbol):  # pragma: no cover — exercised below
        raise RuntimeError("simulated DB outage")

    # Exercise the same try/except-wrap contract used by every real probe.
    async def wrapped_pool(symbol):
        try:
            return await boom(symbol)
        except Exception as exc:
            return admin_api._err(exc)

    monkeypatch.setattr(admin_api, "_probe_pool_status", wrapped_pool)

    response = client.get(
        "/api/admin/symbol-health/DOGE_USDT",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pool_status"]["ok"] is False
    assert "RuntimeError" in body["pool_status"]["error"]
    # The other probes were untouched — endpoint stayed healthy.
    assert body["resolver_diff"]["ok"] is True
