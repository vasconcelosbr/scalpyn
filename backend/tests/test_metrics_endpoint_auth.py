"""Tests for the bearer-token gate on ``/metrics`` (Task #167).

Verifies the three states documented in ``backend/app/api/metrics.py``:

* ``PROMETHEUS_BEARER_TOKEN`` unset  → 404 (endpoint hidden)
* env set, missing/wrong header     → 401 with ``WWW-Authenticate: Bearer``
* env set, correct bearer token     → 200 with the Prometheus exposition body
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api import metrics as metrics_api  # noqa: E402


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(metrics_api.router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch: pytest.MonkeyPatch):
    # Each test sets PROMETHEUS_BEARER_TOKEN explicitly via monkeypatch, so the
    # fixture only guarantees a clean slate beforehand.
    monkeypatch.delenv("PROMETHEUS_BEARER_TOKEN", raising=False)
    yield


def test_metrics_returns_404_when_token_not_configured(
    client: TestClient,
) -> None:
    """Default-deny: the endpoint is invisible until an operator opts in."""
    response = client.get("/metrics")
    assert response.status_code == 404


def test_metrics_returns_404_when_token_is_blank(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace-only token is treated as 'not configured' (defense in depth)."""
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "   ")
    response = client.get("/metrics")
    assert response.status_code == 404


def test_metrics_returns_401_when_authorization_header_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "s3cret-token")
    response = client.get("/metrics")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_metrics_returns_401_when_scheme_is_not_bearer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "s3cret-token")
    response = client.get(
        "/metrics", headers={"Authorization": "Basic czNjcmV0LXRva2Vu"}
    )
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_metrics_returns_401_when_token_is_wrong(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "s3cret-token")
    response = client.get(
        "/metrics", headers={"Authorization": "Bearer not-the-token"}
    )
    assert response.status_code == 401


def test_metrics_returns_200_with_correct_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "s3cret-token")
    response = client.get(
        "/metrics", headers={"Authorization": "Bearer s3cret-token"}
    )
    assert response.status_code == 200
    # Either the prometheus exposition format or the "client not installed"
    # fallback — both are text/plain and indicate the gate let the request
    # through to render_metrics().
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("text/plain")
    assert response.content  # non-empty body


def test_metrics_tolerates_surrounding_whitespace_in_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "s3cret-token")
    response = client.get(
        "/metrics", headers={"Authorization": "Bearer    s3cret-token   "}
    )
    assert response.status_code == 200
