"""Export parity, tenant/auth boundary and read-only contract. No live services."""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import pump_readonly as api
from app.api.config import get_current_user_id
from app.config import settings
from app.services import pump_monitor_engine as engine
from app.services import pump_monitor_service as monitor
from app.services import pump_readonly as service
from app.services import radar_readonly as readonly
from app.services import pump_score_v1 as v1

OWNER, POOL = uuid4(), uuid4()
KEY = "test-only-pump-key"
URL = "/api/integrations/pump-monitor/strong-rising/assets"
NOW = datetime.now(timezone.utc)


def row(symbol, direction="long", strength="forte", score=73):
    return {"symbol": symbol, "direction": {"direction": direction, "strength": strength,
            "reason": "perp_confirma"}, "pump_monitor_score": 11, "only_rising": False,
            "indicators": {"pump_monitor_score": {"value": score}}, "private": "not exported"}


def fixture_cycle():
    config = engine.effective_config({"universe_pool_id": str(POOL), "_meta": {"version": 7}})
    rows = [row("TRX_USDT"), row("ASTR_USDT", strength="normal"),
            row("MNT_USDT", direction="short"), row("LINK_USDT", direction="neutral", strength=None)]
    envelope = {"pool_id": str(POOL), "generated_at": NOW.isoformat(),
                "config_version": config["_meta"]["version"], "config_hash": config["_meta"]["config_hash"],
                "served_from": "redis_latest_cycle", "total_assets": len(rows), "failed_assets": 0,
                "cycle_seconds": config["cycle_seconds"], "rows": rows}
    return config, envelope


class Scalar:
    def __init__(self, value): self.value = value
    def scalar(self): return self.value


class Session:
    def __init__(self, config):
        self.config, self.statements, self.rollbacks, self.owned = config, [], 0, True
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def rollback(self): self.rollbacks += 1
    async def commit(self): pytest.fail("write forbidden")
    async def flush(self): pytest.fail("write forbidden")
    def add(self, *args): pytest.fail("write forbidden")
    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if sql.startswith("SET TRANSACTION"):
            assert len(self.statements) == 1
            assert "REPEATABLE READ, READ ONLY" in sql
            return Scalar(None)
        assert sql.lstrip().startswith("SELECT")
        if "FROM config_profiles" in sql:
            assert params["u"] == str(OWNER)
            return Scalar(self.config)
        if "FROM pools" in sql:
            assert params == {"pool": POOL, "owner": OWNER}
            return Scalar(POOL if self.owned else None)
        pytest.fail("unexpected query")


@pytest.fixture
def setup(monkeypatch):
    config, envelope = fixture_cycle()
    session = Session(config)
    def factory(**kwargs):
        assert kwargs == {"autoflush": False}
        return session
    monkeypatch.setattr(readonly, "AsyncSessionLocal", factory)
    class Redis:
        async def get(self, key):
            assert key == monitor._LATEST_KEY.format(user_id=OWNER)
            return json.dumps(envelope)
        async def set(self, *args, **kwargs): pytest.fail("redis writes forbidden")
    async def redis(): return Redis()
    monkeypatch.setattr(monitor, "_redis", redis)
    monkeypatch.setattr(settings, "PUMP_READONLY_KEY_SHA256", hashlib.sha256(KEY.encode()).hexdigest())
    monkeypatch.setattr(settings, "PUMP_READONLY_USER_ID", str(OWNER))
    app = FastAPI()
    app.include_router(api.router)
    @app.get("/existing-jwt-route")
    def legacy(user=Depends(get_current_user_id)): return {"user": str(user)}
    return TestClient(app), session, envelope


def headers(key=KEY): return {"X-Scalpyn-Service-Key": key}


def test_export_matches_display_and_reads_only(setup):
    client, session, envelope = setup
    before = copy.deepcopy(envelope)
    response = client.get(URL, headers=headers())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["rows"] == [{"symbol": "TRX_USDT", "direction": envelope["rows"][0]["direction"],
                                "pump_monitor_score": 73}]
    assert result["total_assets"] == 4 and result["matched_assets"] == 1
    assert not result["stale"]
    assert "private" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-scalpyn-read-mode"] == "persisted-only"
    assert session.rollbacks == 1
    assert envelope == before


@pytest.mark.parametrize("key", [None, "", "wrong"])
def test_bad_key_never_opens_database(setup, key):
    client, session, _ = setup
    response = client.get(URL, headers=headers(key) if key is not None else {})
    assert response.status_code == 401
    assert not session.statements


def test_duplicate_key_and_jwt_fallback_rejected(setup):
    client, session, _ = setup
    assert client.get(URL, headers=[("X-Scalpyn-Service-Key", KEY)] * 2).status_code == 401
    assert client.get(URL, headers={"Authorization": f"Bearer {KEY}"}).status_code == 401
    assert not session.statements


def test_revocation_and_key_does_not_authorize_existing_routes(setup, monkeypatch):
    client, session, _ = setup
    assert client.get("/existing-jwt-route", headers=headers()).status_code in (401, 403)
    assert client.get("/existing-jwt-route", headers={"Authorization": f"Bearer {KEY}"}).status_code == 401
    monkeypatch.setattr(settings, "PUMP_READONLY_KEY_SHA256", hashlib.sha256(b"rotated").hexdigest())
    assert client.get(URL, headers=headers()).status_code == 401
    monkeypatch.setattr(settings, "PUMP_READONLY_KEY_SHA256", "")
    assert client.get(URL, headers=headers()).status_code == 404
    assert not session.statements


@pytest.mark.parametrize("query", ["user_id=someone_else", "pool_id=other", "limit=1", "only_rising=true"])
def test_cannot_override_export_scope(setup, query):
    client, session, _ = setup
    assert client.get(URL + "?" + query, headers=headers()).status_code == 422
    assert not session.statements


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_no_write_routes(setup, method):
    client, session, _ = setup
    assert getattr(client, method)(URL, headers=headers()).status_code == 405
    assert not session.statements


def test_foreign_pool_rejected_before_cache_read(setup):
    client, session, _ = setup
    session.owned = False
    response = client.get(URL, headers=headers())
    assert response.status_code == 503
    assert response.json()["detail"] == "monitored_pool_unavailable"


def test_all_strong_rows_even_outside_top_and_only_rising_filter():
    config, envelope = fixture_cycle()
    envelope["rows"] = [row(f"ASSET_{i:02}_USDT", score=None) for i in range(25)]
    envelope["total_assets"] = 25
    out = service.project_strong_assets(envelope, config, now=NOW)
    assert len(out.rows) == 25 and out.rows[0].pump_monitor_score is None
    assert [r.symbol for r in out.rows] == sorted(r["symbol"] for r in envelope["rows"])


def test_ml_final_direction_is_preserved_without_recalculation():
    config, envelope = fixture_cycle()
    promoted, demoted = row("PROMOTED"), row("DEMOTED")
    promoted["direction"] = v1.ml_direction({"direction": "long", "strength": "normal", "reason": "so_spot"}, 0.7, v1.DEFAULT_V1)
    demoted["direction"] = v1.ml_direction(demoted["direction"], 0.3, v1.DEFAULT_V1)
    envelope.update(rows=[promoted, demoted], total_assets=2)
    out = service.project_strong_assets(envelope, config, now=NOW)
    assert [r.symbol for r in out.rows] == ["PROMOTED"]
    assert out.rows[0].direction.reason == "so_spot+ml_confirma"


def test_valid_empty_selection_is_not_unavailable():
    config, envelope = fixture_cycle()
    envelope["rows"][0]["direction"]["strength"] = "normal"
    out = service.project_strong_assets(envelope, config, now=NOW)
    assert out.rows == [] and out.matched_assets == 0 and out.total_assets == 4


def test_staleness_uses_existing_display_configuration():
    config, envelope = fixture_cycle()
    boundary = config["cycle_seconds"] * config["display"]["stale_after_cycles"]
    assert not service.project_strong_assets(envelope, config, now=NOW + timedelta(seconds=boundary)).stale
    assert service.project_strong_assets(envelope, config, now=NOW + timedelta(seconds=boundary + 1)).stale


@pytest.mark.parametrize("patch,reason", [
    ({"served_from": "sampled_snapshot_table", "rows": []}, "complete_cycle_unavailable"),
    ({"pool_id": str(uuid4())}, "pool_changed_waiting_for_cycle"),
    ({"config_hash": "other"}, "config_changed_waiting_for_cycle"),
    ({"config_version": 999}, "config_changed_waiting_for_cycle"),
    ({"failed_assets": 1}, "incomplete_cycle"),
    ({"total_assets": 999}, "incomplete_cycle"),
    ({"rows": None}, "incomplete_cycle"),
    ({"generated_at": "invalid"}, "incomplete_cycle"),
    ({"generated_at": "2026-11-21T00:00:00"}, "incomplete_cycle"),
])
def test_unavailable_states_are_explicit(setup, patch, reason):
    client, _, envelope = setup
    envelope.update(patch)
    # latest_envelope annotates cached results, so the sampled case tests projection directly.
    if patch.get("served_from") == "sampled_snapshot_table":
        with pytest.raises(service.PumpExportUnavailable, match=reason):
            service.project_strong_assets(envelope, fixture_cycle()[0])
    else:
        response = client.get(URL, headers=headers())
        assert response.status_code == 503
        assert response.json()["detail"] == reason


def test_missing_cycle_and_missing_direction_are_not_empty_success():
    config, envelope = fixture_cycle()
    with pytest.raises(service.PumpExportUnavailable, match="complete_cycle_unavailable"):
        service.project_strong_assets(None, config)
    del envelope["rows"][0]["direction"]
    with pytest.raises(service.PumpExportUnavailable, match="incomplete_cycle"):
        service.project_strong_assets(envelope, config)


def test_unexpected_failure_is_sanitized(setup, monkeypatch, caplog):
    client, _, _ = setup
    async def broken(*args): raise RuntimeError("secret-value-private-data")
    monkeypatch.setattr(api, "read_strong_assets", broken)
    response = client.get(URL, headers=headers())
    assert response.status_code == 503
    assert "secret-value-private-data" not in response.text + caplog.text


def test_projector_has_no_engine_execution_or_writes():
    import inspect
    source = inspect.getsource(service)
    for forbidden in ("run_cycle(", "evaluate_universe(", "direction_signal(", ".commit(", ".set(", "INSERT ", "UPDATE "):
        assert forbidden not in source
