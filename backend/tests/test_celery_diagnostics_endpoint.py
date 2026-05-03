"""Tests for ``GET /api/system/celery-diagnostics`` (Task #186).

Mirrors the auth contract of ``/metrics`` (Task #167): 404 when the
gating env var is unset, 401 on a bad/missing bearer header, 200 with
a flat JSON snapshot otherwise. Real Redis / Celery dependencies are
stubbed so the test is hermetic.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api import system as system_api  # noqa: E402


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(system_api.router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DIAGNOSTICS_BEARER_TOKEN", raising=False)
    yield


def _stub_internals(monkeypatch):
    """Replace the three sub-probes with deterministic fakes."""
    monkeypatch.setattr(
        system_api, "_redis_probe",
        lambda: {
            "redis_ping": True,
            "redis_dbsize": 42,
            "last_collect_all_start": "2026-05-03T00:00:00+00:00",
            "last_collect_all_end": "2026-05-03T00:00:05+00:00",
            "collect_all_runs": "10",
            "collect_all_errors": "1",
            "last_collect_all_error": None,
            "error": None,
        },
    )
    monkeypatch.setattr(
        system_api, "_scan_celery_processes",
        lambda: {"worker_processes": [101], "beat_processes": [102], "error": None},
    )
    monkeypatch.setattr(
        system_api, "_inspect_celery",
        lambda: {
            "inspect_active": {"celery@worker1": []},
            "inspect_registered": ["app.tasks.collect_market_data.collect_all"],
            "inspect_stats": {"celery@worker1": {"total": {"collect_all": 10}}},
            "error": None,
        },
    )


def test_celery_diagnostics_404_when_token_unset(client: TestClient) -> None:
    response = client.get("/api/system/celery-diagnostics")
    assert response.status_code == 404


def test_celery_diagnostics_401_when_token_missing(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("DIAGNOSTICS_BEARER_TOKEN", "s3cret")
    response = client.get("/api/system/celery-diagnostics")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate", "").lower().startswith("bearer")


def test_celery_diagnostics_401_when_token_wrong(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("DIAGNOSTICS_BEARER_TOKEN", "s3cret")
    response = client.get(
        "/api/system/celery-diagnostics",
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_celery_diagnostics_returns_snapshot_when_authed(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("DIAGNOSTICS_BEARER_TOKEN", "s3cret")
    _stub_internals(monkeypatch)

    response = client.get(
        "/api/system/celery-diagnostics",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    body = response.json()

    # Required top-level keys for the runbook field map.
    for key in (
        "timestamp", "redis_ping", "redis_dbsize",
        "worker_processes", "beat_processes",
        "inspect_active", "inspect_registered",
        "last_collect_all_start", "last_collect_all_end",
        "collect_all_runs", "collect_all_errors",
    ):
        assert key in body, f"missing {key}"
    assert body["redis_ping"] is True
    assert body["worker_processes"] == [101]
    assert body["beat_processes"] == [102]
    assert "dispatch" not in body  # only present when ?dispatch=...


def test_celery_diagnostics_rejects_unknown_dispatch(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("DIAGNOSTICS_BEARER_TOKEN", "s3cret")
    _stub_internals(monkeypatch)

    response = client.get(
        "/api/system/celery-diagnostics?dispatch=evil_task",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 400


def test_celery_diagnostics_dispatch_collect_all(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("DIAGNOSTICS_BEARER_TOKEN", "s3cret")
    _stub_internals(monkeypatch)

    fake_async = SimpleNamespace(id="task-uuid-123", state="STARTED", traceback=None)

    class _FakeCeleryApp:
        def send_task(self, name, *a, **kw):
            assert name == "app.tasks.collect_market_data.collect_all"
            return fake_async

    fake_celery_module = SimpleNamespace(celery_app=_FakeCeleryApp())
    monkeypatch.setitem(sys.modules, "app.tasks.celery_app", fake_celery_module)

    # Skip the real 3 s sleep — patch time.sleep used inside the endpoint.
    monkeypatch.setattr(system_api._time, "sleep", lambda _s: None)

    response = client.get(
        "/api/system/celery-diagnostics?dispatch=collect_all",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dispatch"]["dispatched_task_id"] == "task-uuid-123"
    assert body["dispatch"]["state_after_3s"] == "STARTED"
    assert body["dispatch"]["error"] is None


def test_redis_url_never_appears_in_response(
    client: TestClient, monkeypatch
) -> None:
    """Defense-in-depth: the broker URL carries the password — the
    diagnostics payload must never echo it, even when probes succeed."""
    monkeypatch.setenv("DIAGNOSTICS_BEARER_TOKEN", "s3cret")
    monkeypatch.setenv(
        "REDIS_URL",
        "redis://default:SUPER_SECRET_PWD@host.example.com:6379/0",
    )
    _stub_internals(monkeypatch)

    response = client.get(
        "/api/system/celery-diagnostics",
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    raw = response.text
    assert "SUPER_SECRET_PWD" not in raw
    assert "redis://" not in raw
