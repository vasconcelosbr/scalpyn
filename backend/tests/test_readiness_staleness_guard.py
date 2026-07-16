"""P3 Fase 1.7 — guard de staleness do readiness.

O job de certificação pode morrer em silêncio; o endpoint /ml/readiness/latest
não pode servir a última run como "atual" quando ela está velha. Run acima do
threshold → status_effective=STALE (status original preservado); run recente →
status original. Sem threshold configurado → STALE (fail-closed).
"""
from datetime import datetime, timedelta, timezone

from app.services.ml_data_certification_service import (
    _resolve_staleness_threshold_hours,
    compute_readiness_staleness,
)

_NOW = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)


# ── threshold resolver (Zero Hardcode, fail-closed) ──────────────────────────

def test_threshold_present():
    assert _resolve_staleness_threshold_hours({"ml_readiness_staleness_threshold_hours": 3}) == 3


def test_threshold_missing_or_invalid_returns_none():
    assert _resolve_staleness_threshold_hours({}) is None
    assert _resolve_staleness_threshold_hours({"ml_readiness_staleness_threshold_hours": 0}) is None
    assert _resolve_staleness_threshold_hours({"ml_readiness_staleness_threshold_hours": True}) is None
    assert _resolve_staleness_threshold_hours({"ml_readiness_staleness_threshold_hours": "x"}) is None


# ── staleness ────────────────────────────────────────────────────────────────

def test_recent_run_keeps_original_status():
    r = compute_readiness_staleness(_NOW - timedelta(hours=1), "RED", 3, now=_NOW)
    assert r["is_stale"] is False
    assert r["status_effective"] == "RED"  # original preservado, não mascarado


def test_old_run_is_stale():
    r = compute_readiness_staleness(_NOW - timedelta(hours=27), "GREEN", 3, now=_NOW)
    assert r["is_stale"] is True
    assert r["status_effective"] == "STALE"
    assert r["run_age_hours"] == 27.0


def test_exactly_at_threshold_is_fresh():
    r = compute_readiness_staleness(_NOW - timedelta(hours=3), "GREEN", 3, now=_NOW)
    assert r["is_stale"] is False
    assert r["status_effective"] == "GREEN"


def test_missing_threshold_is_stale_fail_closed():
    r = compute_readiness_staleness(_NOW - timedelta(minutes=1), "GREEN", None, now=_NOW)
    assert r["is_stale"] is True
    assert r["status_effective"] == "STALE"
