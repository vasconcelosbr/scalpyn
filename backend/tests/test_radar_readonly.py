"""Contract tests: no production DB, credentials, network or pipeline execution."""
import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import radar_readonly as api
from app.api.config import get_current_user_id
from app.config import settings
from app.services import radar_readonly as service

OWNER, WL, POOL = uuid4(), uuid4(), uuid4()
KEY = "test-only-radar-key"
URL = f"/api/integrations/radar/watchlists/{WL}/assets"
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)


class Result:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def first(self): return self.rows[0] if self.rows else None
    def all(self): return self.rows


class Session:
    def __init__(self, *, missing=False, empty=False):
        self.statements = []
        self.rollbacks = 0
        self.missing, self.empty = missing, empty
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def rollback(self): self.rollbacks += 1
    async def commit(self): pytest.fail("commit forbidden")
    async def flush(self): pytest.fail("flush forbidden")
    def add(self, *args): pytest.fail("add forbidden")
    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        assert sql.lstrip().startswith(("SELECT", "WITH RECURSIVE", "SET TRANSACTION")), sql
        if sql.startswith("SET TRANSACTION"):
            assert len(self.statements) == 1
            assert "REPEATABLE READ, READ ONLY" in sql
            return Result([])
        if "FROM pipeline_watchlists " in sql:
            assert params == {"id": WL, "owner": OWNER}
            return Result([] if self.missing else [dict(id=WL, name="POOL", level="POOL",
                market_mode="spot", source_pool_id=POOL, source_watchlist_id=None,
                last_scanned_at=OLD, queried_at=NOW)])
        if "FROM pipeline_watchlist_assets" in sql:
            assert "level_direction IS NULL OR level_direction = 'up'" in sql
            assert "ORDER BY symbol, id" in sql and "LIMIT" not in sql
            return Result([] if self.empty else [dict(id=uuid4(), symbol="AKE_USDT",
                current_price=None, price_change_24h=0, volume_24h=None, market_cap=None,
                alpha_score=12.5, entered_at=OLD, refreshed_at=OLD,
                analysis_snapshot={"persisted": True, "score": 9})])
        assert "FROM indicators" in sql and params["market_type"] == "spot"
        assert "scheduler_group > g.scheduler_group" in sql
        return Result([dict(symbol="AKE_USDT", scheduler_group="structural", timeframe="5m",
                            time=OLD, indicators_json={"rsi": {"value": 42, "status": "VALID"}})])


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "RADAR_READONLY_KEY_SHA256", hashlib.sha256(KEY.encode()).hexdigest())
    monkeypatch.setattr(settings, "RADAR_READONLY_USER_ID", str(OWNER))
    monkeypatch.setattr(settings, "RADAR_READONLY_WATCHLIST_IDS", str(WL))
    session = Session()
    def factory(**kwargs):
        assert kwargs == {"autoflush": False}
        return session
    monkeypatch.setattr(service, "AsyncSessionLocal", factory)
    app = FastAPI()
    app.include_router(api.router)
    @app.get("/legacy-auth")
    def legacy(user=Depends(get_current_user_id)): return {"user": str(user)}
    return TestClient(app), session


def headers(key=KEY): return {"X-Scalpyn-Service-Key": key}


@pytest.mark.parametrize("key", [None, "", "wrong"])
def test_bad_key_no_connection(setup, key):
    client, session = setup
    response = client.get(URL, headers=headers(key) if key is not None else {})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    assert not session.statements


def test_disabled(setup, monkeypatch):
    monkeypatch.setattr(settings, "RADAR_READONLY_KEY_SHA256", "")
    client, session = setup
    assert client.get(URL, headers=headers()).status_code == 404
    assert not session.statements


@pytest.mark.parametrize("field,value", [
    ("RADAR_READONLY_KEY_SHA256", "invalid"),
    ("RADAR_READONLY_USER_ID", ""),
    ("RADAR_READONLY_USER_ID", "bad"),
    ("RADAR_READONLY_WATCHLIST_IDS", ""),
    ("RADAR_READONLY_WATCHLIST_IDS", f"{WL},bad"),
])
def test_config_fail_closed(setup, monkeypatch, field, value):
    monkeypatch.setattr(settings, field, value)
    client, session = setup
    response = client.get(URL, headers=headers())
    assert response.status_code == 500
    assert response.json() == {"detail": "Service configuration unavailable"}
    assert not session.statements


def test_allowlist_and_owner(setup, monkeypatch):
    client, session = setup
    monkeypatch.setattr(settings, "RADAR_READONLY_WATCHLIST_IDS", str(uuid4()))
    assert client.get(URL, headers=headers()).status_code == 404
    assert not session.statements
    monkeypatch.setattr(settings, "RADAR_READONLY_WATCHLIST_IDS", str(WL))
    session.missing = True
    assert client.get(URL, headers=headers()).status_code == 404
    assert session.rollbacks == 1


def test_persisted_response_and_no_side_effects(setup, monkeypatch, caplog):
    from starlette.background import BackgroundTasks
    import httpx
    def forbidden(*args, **kwargs): pytest.fail("side effect forbidden")
    monkeypatch.setattr(BackgroundTasks, "add_task", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    caplog.set_level(logging.INFO, logger=api.__name__)
    client, session = setup
    response = client.get(URL, headers=headers())
    assert response.status_code == 200
    data = response.json()
    assert data["contract_version"] == "radar_watchlist_v1"
    assert data["served_by"] == "service_readonly" and data["asset_count"] == 1
    assert data["last_scanned_at"].startswith("2020-")
    asset = data["assets"][0]
    assert asset["current_price"] is None and asset["price_change_24h"] == 0
    assert asset["alpha_score"] == 12.5 and asset["analysis_snapshot"]["score"] == 9
    reading = asset["indicator_readings"][0]
    assert reading["values"] == {"rsi": {"value": 42, "status": "VALID"}}
    assert reading["freshness"] == "not_assessed"
    assert reading["age_seconds"] == (NOW - OLD).total_seconds()
    assert session.rollbacks == 1
    assert response.headers["x-scalpyn-read-mode"] == "persisted-only"
    assert KEY not in caplog.text and settings.RADAR_READONLY_KEY_SHA256 not in caplog.text
    assert "service_principal=radar_readonly" in caplog.text


def test_empty_no_refresh(setup):
    client, session = setup
    session.empty = True
    response = client.get(URL, headers=headers())
    assert response.status_code == 200
    assert response.json()["assets"] == []
    assert len(session.statements) == 3


def test_missing_indicator_rows_preserve_asset(setup, monkeypatch):
    client, session = setup
    original = session.execute
    async def execute(statement, params=None):
        if "WITH RECURSIVE requested" in str(statement):
            return Result([])
        return await original(statement, params)
    monkeypatch.setattr(session, "execute", execute)
    response = client.get(URL, headers=headers())
    assert response.status_code == 200
    assert response.json()["assets"][0]["indicator_readings"] == []


def test_database_error_is_not_empty_success(setup, monkeypatch, caplog):
    from sqlalchemy.exc import SQLAlchemyError
    client, session = setup
    async def fail(*args): raise SQLAlchemyError(KEY)
    monkeypatch.setattr(api, "read_watchlist", fail)
    response = client.get(URL, headers=headers())
    assert response.status_code == 503
    assert response.json() == {"detail": "Radar export unavailable"}
    assert response.headers["cache-control"] == "private, no-store"
    assert KEY not in caplog.text
    assert session.rollbacks == 1


def test_jwt_separation(setup):
    client, session = setup
    token = jwt.encode({"sub": str(OWNER), "type": "access"}, settings.JWT_SECRET, algorithm="HS256")
    bearer = {"Authorization": f"Bearer {token}"}
    assert client.get("/legacy-auth", headers=bearer).json() == {"user": str(OWNER)}
    assert client.get("/legacy-auth", headers=headers()).status_code == client.get("/legacy-auth").status_code
    assert client.get("/legacy-auth", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get(URL, headers=bearer).status_code == 401
    assert client.get(URL, headers={**bearer, **headers("wrong")}).status_code == 401
    assert client.get(URL, headers={**bearer, **headers()}).status_code == 200


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch", "head"])
def test_no_mutation_route(setup, method):
    client, session = setup
    assert getattr(client, method)(URL, headers=headers()).status_code == 405
    assert not session.statements


def test_duplicate_header_rejected(setup):
    client, session = setup
    assert client.get(URL, headers=[("X-Scalpyn-Service-Key", KEY)] * 2).status_code == 401
    assert not session.statements


def test_session_rollback_on_exception(monkeypatch):
    session = Session()
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda **kwargs: session)
    async def run():
        with pytest.raises(RuntimeError):
            async with service.readonly_session(): raise RuntimeError("test")
    asyncio.run(run())
    assert session.rollbacks == 1


def test_actual_application_router_isolation(setup, monkeypatch):
    from app.main import app
    from app.api import watchlists, pools
    client, session = setup
    # TestClient without a context manager deliberately does not run startup
    # schedulers. Test the real router graph and auth before any DB access.
    actual = TestClient(app)
    new = next(r for r in app.routes if getattr(r, "path", "") == URL.replace(str(WL), "{watchlist_id}"))
    assert new.endpoint is api.radar_assets
    old = next(r for r in app.routes if getattr(r, "path", "") == "/api/watchlists/{watchlist_id}/assets")
    assert old.endpoint is watchlists.get_watchlist_assets
    assert actual.get(URL, headers=headers()).status_code == 200
    for path in [f"/api/watchlists/{WL}/assets", f"/api/watchlists/{WL}", "/api/pools/"]:
        unauthenticated = actual.get(path)
        service_only = actual.get(path, headers=headers())
        assert service_only.status_code == unauthenticated.status_code
        assert service_only.status_code in {401, 403, 405}
        assert service_only.json() == unauthenticated.json()
    for method in ["post", "put", "delete"]:
        response = actual.request(method.upper(), f"/api/watchlists/{WL}", headers=headers(), json={})
        assert response.status_code in {401, 403, 405}
