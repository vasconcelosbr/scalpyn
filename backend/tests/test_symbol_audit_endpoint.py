"""Tests for ``POST /api/admin/diagnostics/symbol-audit`` (Task #194).

Mirrors the auth contract enforced for ``/api/admin/symbol-health/{symbol}``
and exercises the request → SymbolHealthService → SymbolRemediator wiring
without hitting the real DB / Redis / exchange.
"""

from __future__ import annotations

import asyncio
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


def test_symbol_audit_404_when_token_unset(client: TestClient) -> None:
    response = client.post("/api/admin/diagnostics/symbol-audit", json={"dry_run": True})
    assert response.status_code == 404


def test_symbol_audit_401_when_token_wrong(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    response = client.post(
        "/api/admin/diagnostics/symbol-audit",
        json={"dry_run": True},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def _stub_pipeline(monkeypatch):
    from app.services.symbol_health_service import (
        STATUS_NOT_APPROVED,
        STATUS_OK,
        SymbolHealth,
        SymbolHealthReport,
    )
    from app.services import symbol_remediator as rem_mod

    fake_report = SymbolHealthReport(
        checked_at="2026-05-03T00:00:00+00:00",
        total=2,
        counts={STATUS_OK: 1, STATUS_NOT_APPROVED: 1},
        symbols=[
            SymbolHealth(symbol="BTC_USDT", status=STATUS_OK),
            SymbolHealth(symbol="ZZZ_USDT", status=STATUS_NOT_APPROVED),
        ],
    )

    async def fake_audit(self, symbols=None):
        return fake_report

    async def fake_remediate(self, report, dry_run=False):
        return rem_mod.RemediationReport(
            dry_run=dry_run,
            total_actions=1,
            counts_by_action={rem_mod.ACTION_APPROVE: 1},
            actions=[
                rem_mod.RemediationAction(
                    symbol="ZZZ_USDT",
                    action=rem_mod.ACTION_APPROVE,
                    reason="NOT_APPROVED",
                    executed=not dry_run,
                ),
            ],
        )

    from app.services import symbol_health_service as hs_mod
    monkeypatch.setattr(hs_mod.SymbolHealthService, "audit", fake_audit)
    monkeypatch.setattr(rem_mod.SymbolRemediator, "remediate", fake_remediate)


def test_symbol_audit_dry_run_returns_full_payload(client, monkeypatch):
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    _stub_pipeline(monkeypatch)

    response = client.post(
        "/api/admin/diagnostics/symbol-audit",
        json={"dry_run": True},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["total"] == 2
    assert body["remediation"]["dry_run"] is True
    assert body["remediation"]["counts_by_action"]["approve"] == 1
    # In dry-run no action should be marked executed.
    assert all(a["executed"] is False for a in body["remediation"]["actions"])


def test_symbol_audit_executes_when_dry_run_false(client, monkeypatch):
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    _stub_pipeline(monkeypatch)

    response = client.post(
        "/api/admin/diagnostics/symbol-audit",
        json={"dry_run": False, "no_approve": False},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["remediation"]["dry_run"] is False
    assert all(a["executed"] is True for a in body["remediation"]["actions"])


def test_symbol_audit_accepts_explicit_symbol_filter(client, monkeypatch):
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")

    captured = {}

    async def fake_audit(self, symbols=None):
        captured["symbols"] = list(symbols) if symbols is not None else None
        from app.services.symbol_health_service import SymbolHealthReport
        return SymbolHealthReport(
            checked_at="2026-05-03T00:00:00+00:00",
            total=0,
            counts={},
            symbols=[],
        )

    async def fake_remediate(self, report, dry_run=False):
        from app.services.symbol_remediator import RemediationReport
        return RemediationReport(
            dry_run=dry_run, total_actions=0, counts_by_action={}, actions=[],
        )

    from app.services import symbol_health_service as hs_mod
    from app.services import symbol_remediator as rem_mod
    monkeypatch.setattr(hs_mod.SymbolHealthService, "audit", fake_audit)
    monkeypatch.setattr(rem_mod.SymbolRemediator, "remediate", fake_remediate)

    response = client.post(
        "/api/admin/diagnostics/symbol-audit",
        json={"dry_run": True, "symbols": ["btc_usdt", "ETH_USDT"]},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    assert captured["symbols"] == ["btc_usdt", "ETH_USDT"]


def test_symbol_audit_response_top_level_contract_is_locked(client, monkeypatch):
    """Lock the endpoint shape: top-level keys must always be exactly
    {report, remediation, etapa8} so downstream consumers can rely on
    the contract without optional fields drifting in/out."""
    monkeypatch.setenv("ADMIN_DIAGNOSTICS_TOKEN", "s3cret")
    _stub_pipeline(monkeypatch)

    response = client.post(
        "/api/admin/diagnostics/symbol-audit",
        json={"dry_run": True},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    body = response.json()
    # Top-level shape = Etapa-8 envelope flattened + report/remediation
    # debug nesting. Locked here so future changes are deliberate.
    assert set(body.keys()) == {
        "resumo", "lista", "system_healthy", "report", "remediation",
    }
    assert set(body["resumo"].keys()) == {"total", "corrigidos", "pendentes"}
