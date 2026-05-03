"""Tests for SymbolHealthService classifier + SymbolRemediator (Task #194).

The classifier is exercised against synthetic probe inputs so the
priority hierarchy is locked in: each test asserts exactly one status
is returned for one canonical input combination.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.symbol_health_service import (  # noqa: E402
    DEFAULT_BUFFER_NEWEST_MAX_AGE_SECONDS,
    DEFAULT_INDICATOR_MAX_AGE_SECONDS,
    STATUS_NO_INDICATOR_DATA,
    STATUS_NO_REDIS_DATA,
    STATUS_NOT_APPROVED,
    STATUS_NOT_SUBSCRIBED,
    STATUS_OK,
    STATUS_PRIORITY,
    SymbolHealthReport,
    _classify,
)


def _classify_with_defaults(**kw):
    base = dict(
        symbol="BTC_USDT",
        pool={"is_approved": True, "is_active": True, "exists": True},
        in_ws=True,
        buf={"member_count": 10, "newest_age_seconds": 1.0, "error": None},
        ind={
            "age_seconds": 60.0,
            "has_taker_ratio": True,
            "has_volume_delta": True,
            "error": None,
        },
        indicator_max_age=DEFAULT_INDICATOR_MAX_AGE_SECONDS,
        buffer_newest_max_age=DEFAULT_BUFFER_NEWEST_MAX_AGE_SECONDS,
    )
    base.update(kw)
    return _classify(**base)


def test_status_priority_orders_most_blocking_first():
    assert STATUS_PRIORITY[0] == STATUS_NOT_APPROVED
    assert STATUS_PRIORITY[-1] == STATUS_OK


def test_classify_ok_when_everything_is_healthy():
    rec = _classify_with_defaults()
    assert rec.status == STATUS_OK
    assert rec.is_approved is True
    assert rec.has_taker_ratio is True


def test_classify_not_approved_takes_priority_over_everything():
    rec = _classify_with_defaults(
        pool={"is_approved": False, "is_active": True, "exists": True},
    )
    assert rec.status == STATUS_NOT_APPROVED


def test_classify_inactive_row_routes_to_inactive_status():
    """Etapa 1 of round-2 review: inactive rows are operator-disabled,
    NOT a pool inconsistency. They must route to STATUS_INACTIVE so
    the rule-1 monitor doesn't flag them.
    """
    from app.services.symbol_health_service import STATUS_INACTIVE
    rec = _classify_with_defaults(
        pool={"is_approved": True, "is_active": False, "exists": True},
    )
    assert rec.status == STATUS_INACTIVE
    # And approved-but-inactive must NOT count as approved.
    assert rec.is_approved is False


def test_classify_not_subscribed_when_approved_but_missing_from_ws():
    rec = _classify_with_defaults(in_ws=False)
    assert rec.status == STATUS_NOT_SUBSCRIBED


def test_classify_no_redis_data_when_buffer_empty():
    rec = _classify_with_defaults(
        buf={"member_count": 0, "newest_age_seconds": None, "error": None},
    )
    assert rec.status == STATUS_NO_REDIS_DATA


def test_classify_no_redis_data_when_buffer_only_holds_stale_trades():
    stale_age = DEFAULT_BUFFER_NEWEST_MAX_AGE_SECONDS + 60
    rec = _classify_with_defaults(
        buf={"member_count": 5, "newest_age_seconds": stale_age, "error": None},
    )
    assert rec.status == STATUS_NO_REDIS_DATA


def test_classify_no_indicator_when_taker_ratio_missing():
    rec = _classify_with_defaults(
        ind={
            "age_seconds": 60.0,
            "has_taker_ratio": False,
            "has_volume_delta": True,
            "error": None,
        },
    )
    assert rec.status == STATUS_NO_INDICATOR_DATA


def test_classify_no_indicator_when_volume_delta_missing():
    rec = _classify_with_defaults(
        ind={
            "age_seconds": 60.0,
            "has_taker_ratio": True,
            "has_volume_delta": False,
            "error": None,
        },
    )
    assert rec.status == STATUS_NO_INDICATOR_DATA


def test_classify_no_indicator_when_row_too_old():
    rec = _classify_with_defaults(
        ind={
            "age_seconds": DEFAULT_INDICATOR_MAX_AGE_SECONDS + 60,
            "has_taker_ratio": True,
            "has_volume_delta": True,
            "error": None,
        },
    )
    assert rec.status == STATUS_NO_INDICATOR_DATA


def test_classify_carries_probe_errors_into_record():
    rec = _classify_with_defaults(
        buf={"member_count": 0, "newest_age_seconds": None, "error": "redis_unavailable"},
    )
    assert rec.status == STATUS_NO_REDIS_DATA
    assert any("redis_unavailable" in e for e in rec.probe_errors)


def test_report_counts_and_to_dict_round_trip():
    from app.services.symbol_health_service import SymbolHealth

    report = SymbolHealthReport(
        checked_at="2026-05-03T00:00:00+00:00",
        total=2,
        counts={STATUS_OK: 1, STATUS_NOT_APPROVED: 1},
        symbols=[
            SymbolHealth(symbol="BTC_USDT", status=STATUS_OK),
            SymbolHealth(symbol="ZZZ_USDT", status=STATUS_NOT_APPROVED),
        ],
    )
    payload = report.to_dict()
    assert payload["total"] == 2
    assert payload["counts"][STATUS_OK] == 1
    assert payload["symbols"][0]["symbol"] == "BTC_USDT"


# ── Remediator unit tests ───────────────────────────────────────────────


def _build_report(*records) -> SymbolHealthReport:
    from app.services.symbol_health_service import SymbolHealth

    counts = {s: 0 for s in STATUS_PRIORITY}
    syms = []
    for symbol, status in records:
        rec = SymbolHealth(symbol=symbol, status=status)
        rec.is_approved = status != STATUS_NOT_APPROVED
        rec.pool_row_exists = True
        syms.append(rec)
        counts[status] = counts.get(status, 0) + 1
    return SymbolHealthReport(
        checked_at="2026-05-03T00:00:00+00:00",
        total=len(syms),
        counts=counts,
        symbols=syms,
    )


class _FakeValidator:
    def __init__(self, tradable=True, fail=False):
        self._tradable = tradable
        self.last_load_failed = fail

    async def is_tradable(self, symbol):
        return self._tradable


def test_remediator_dry_run_never_executes_any_action(monkeypatch):
    from app.services import symbol_remediator as rem_mod

    report = _build_report(
        ("BTC_USDT", STATUS_NOT_APPROVED),
        ("ETH_USDT", STATUS_NOT_SUBSCRIBED),
        ("SOL_USDT", STATUS_NO_REDIS_DATA),
        ("DOGE_USDT", STATUS_NO_INDICATOR_DATA),
    )

    async def boom(*a, **kw):
        raise AssertionError("dry-run path executed a side effect")

    monkeypatch.setattr(rem_mod, "_bulk_approve", boom)
    monkeypatch.setattr(rem_mod, "_retry_buffer", boom)

    remediator = rem_mod.SymbolRemediator(validator=_FakeValidator())
    out = asyncio.run(remediator.remediate(report, dry_run=True))

    assert out.dry_run is True
    assert out.refresh_subscriptions_requested is False
    assert out.recompute_enqueued is False
    assert out.counts_by_action.get(rem_mod.ACTION_APPROVE) == 1
    assert out.counts_by_action.get(rem_mod.ACTION_REFRESH_WS) == 1
    assert out.counts_by_action.get(rem_mod.ACTION_RETRY_BUFFER) == 1
    # Round-2 review: recompute trigger now also covers NOT_SUBSCRIBED
    # (after WS refresh, indicator row may be missing entirely), so the
    # report has one recompute action for NO_INDICATOR_DATA + one for
    # ETH_USDT (NOT_SUBSCRIBED).
    assert out.counts_by_action.get(rem_mod.ACTION_RECOMPUTE_INDICATORS) == 2
    for action in out.actions:
        assert action.executed is False


def test_remediator_removes_symbols_not_tradable_on_gate(monkeypatch):
    """Etapa 4: par sumiu da exchange → DELETE de pool_coins, não skip silencioso."""
    from app.services import symbol_remediator as rem_mod

    report = _build_report(("DELISTED_USDT", STATUS_NOT_APPROVED))
    remediator = rem_mod.SymbolRemediator(validator=_FakeValidator(tradable=False))
    out = asyncio.run(remediator.remediate(report, dry_run=True))

    # Use the new canonical name; the old alias still works for callers
    # that haven't migrated, but new tests track the new constant.
    assert out.counts_by_action.get(rem_mod.ACTION_REMOVE_FROM_POOL) == 1
    assert out.counts_by_action.get(rem_mod.ACTION_APPROVE) is None
    # Backwards-compat alias must still resolve to the same action so
    # any external scripts importing the old name don't silently miss
    # remove events in their dashboards.
    assert rem_mod.ACTION_SKIP_NOT_TRADABLE == rem_mod.ACTION_REMOVE_FROM_POOL


def test_remediator_executes_bulk_approve_then_requests_refresh(monkeypatch):
    from app.services import symbol_remediator as rem_mod

    captured = {}

    async def fake_bulk(db, symbols):
        captured["bulk"] = list(symbols)
        return len(captured["bulk"])

    async def fake_refresh():
        captured["refresh"] = True
        return {"requested": True, "ts_ms": 1}

    fake_session = type("S", (), {"__aenter__": lambda s: _aio(s),
                                   "__aexit__": lambda s, *a: _aio(None)})()

    async def _aio(v):
        return v

    async def fake_verify(db, syms):
        # Per-symbol verification mock: every approve target is
        # confirmed approved+active+spot post-update.
        return set(syms)

    monkeypatch.setattr(rem_mod, "_bulk_approve", fake_bulk)
    monkeypatch.setattr(rem_mod, "_verify_approved", fake_verify)

    class _SessionCtx:
        async def __aenter__(self_inner):
            return self_inner
        async def __aexit__(self_inner, *a):
            return False

    def _factory():
        return _SessionCtx()

    import app.database as db_mod
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", _factory)

    from app.services import gate_ws_leader as leader_mod
    monkeypatch.setattr(leader_mod, "refresh_subscriptions", fake_refresh)

    # No NO_REDIS_DATA / NO_INDICATOR_DATA so retry_buffer / recompute are
    # never invoked — keeps the test hermetic without needing Redis or Celery.
    report = _build_report(
        ("BTC_USDT", STATUS_NOT_APPROVED),
        ("ETH_USDT", STATUS_NOT_APPROVED),
    )
    remediator = rem_mod.SymbolRemediator(
        validator=_FakeValidator(),
        recompute_indicators=False,
    )
    out = asyncio.run(remediator.remediate(report, dry_run=False))

    assert sorted(captured["bulk"]) == ["BTC_USDT", "ETH_USDT"]
    assert captured.get("refresh") is True
    assert out.refresh_subscriptions_requested is True
    approve_actions = [a for a in out.actions if a.action == rem_mod.ACTION_APPROVE]
    assert all(a.executed for a in approve_actions)


# ── Etapa 8 envelope snapshot (Task #194) ───────────────────────────────


def test_build_etapa8_envelope_snapshot_audit_only():
    """Envelope must keep its shape — operator panels parse this contract."""
    from app.services.symbol_health_service import build_etapa8_envelope

    report = _build_report(
        ("BTC_USDT", STATUS_OK),
        ("ETH_USDT", STATUS_NOT_APPROVED),
        ("XRP_USDT", STATUS_NO_REDIS_DATA),
    )
    env = build_etapa8_envelope(report, remediation=None)

    assert set(env.keys()) == {"resumo", "lista", "system_healthy"}
    assert env["resumo"] == {"total": 3, "corrigidos": 0, "pendentes": 2}
    assert env["system_healthy"] is False
    assert [item["symbol"] for item in env["lista"]] == ["BTC_USDT", "ETH_USDT", "XRP_USDT"]
    for item in env["lista"]:
        assert set(item.keys()) == {"symbol", "problema", "ação_aplicada", "status_final"}
    assert env["lista"][0]["status_final"] == "ok"
    assert env["lista"][0]["ação_aplicada"] == "nenhuma"
    assert env["lista"][1]["status_final"] == "pendente"
    assert env["lista"][1]["problema"] == "ativo mas não aprovado em pool_coins"
    assert env["lista"][1]["ação_aplicada"] == "pendente"


def test_build_etapa8_envelope_marks_corrigidos_when_remediation_executed():
    from app.services.symbol_health_service import build_etapa8_envelope
    from app.services import symbol_remediator as rem_mod

    report = _build_report(
        ("BTC_USDT", STATUS_OK),
        ("ETH_USDT", STATUS_NOT_APPROVED),
    )
    rem = rem_mod.RemediationReport(
        dry_run=False,
        total_actions=1,
        counts_by_action={rem_mod.ACTION_APPROVE: 1},
        actions=[
            rem_mod.RemediationAction(
                symbol="ETH_USDT",
                action=rem_mod.ACTION_APPROVE,
                reason="executed",
                executed=True,
            ),
        ],
        refresh_subscriptions_requested=False,
        recompute_enqueued=False,
    )
    env = build_etapa8_envelope(report, rem)

    assert env["resumo"] == {"total": 2, "corrigidos": 1, "pendentes": 0}
    assert env["system_healthy"] is True
    eth = next(i for i in env["lista"] if i["symbol"] == "ETH_USDT")
    assert eth["ação_aplicada"] == rem_mod.ACTION_APPROVE
    assert eth["status_final"] == "corrigido"


# ── Round-2 review fixes (Task #194) ────────────────────────────────────


def test_envelope_inactive_rows_are_ok_not_pendente():
    """STATUS_INACTIVE must not contribute to pendente/corrigido — operator
    intent, not a pool inconsistency."""
    from app.services.symbol_health_service import (
        STATUS_INACTIVE,
        build_etapa8_envelope,
    )

    report = _build_report(
        ("BTC_USDT", STATUS_OK),
        ("OLD_USDT", STATUS_INACTIVE),
        ("ETH_USDT", STATUS_NOT_APPROVED),
    )
    env = build_etapa8_envelope(report, remediation=None)

    assert env["resumo"]["pendentes"] == 1   # only ETH_USDT
    assert env["resumo"]["corrigidos"] == 0
    inactive = next(i for i in env["lista"] if i["symbol"] == "OLD_USDT")
    assert inactive["status_final"] == "ok"
    assert inactive["ação_aplicada"] == "nenhuma"


def test_streaming_health_alert_filters_to_strict_zcard_zero():
    """Rule-2 must fire only on ZCARD==0 + is_approved (not on stale buffer)."""
    import fakeredis.aioredis
    from app.services.symbol_health_service import (
        STATUS_NO_REDIS_DATA,
        SymbolHealth,
        SymbolHealthReport,
        STATUS_PRIORITY,
    )
    from app.tasks.symbol_health_audit import (
        _evaluate_streaming_health,
        _WS_NOT_STREAMING_GRACE_SECONDS,
    )

    counts = {s: 0 for s in STATUS_PRIORITY}

    rec_zcard_zero = SymbolHealth(
        symbol="EMPTY_USDT",
        status=STATUS_NO_REDIS_DATA,
        is_approved=True,
        buffer_member_count=0,
    )
    rec_stale_only = SymbolHealth(
        symbol="STALE_USDT",
        status=STATUS_NO_REDIS_DATA,
        is_approved=True,
        buffer_member_count=42,           # buffer NOT empty, just stale
    )
    rec_unapproved = SymbolHealth(
        symbol="UNAPPR_USDT",
        status=STATUS_NO_REDIS_DATA,
        is_approved=False,                # not approved → never page
        buffer_member_count=0,
    )
    counts[STATUS_NO_REDIS_DATA] = 3

    report = SymbolHealthReport(
        checked_at="2026-05-03T00:00:00+00:00",
        total=3,
        counts=counts,
        symbols=[rec_zcard_zero, rec_stale_only, rec_unapproved],
    )

    async def _drive():
        redis = fakeredis.aioredis.FakeRedis()
        # First pass stamps first-seen — no alert yet.
        n0 = await _evaluate_streaming_health(redis, report)
        assert n0 == 0
        # Pretend the symbol has been empty for > 120s by rewinding the marker.
        old = int((time.time() - _WS_NOT_STREAMING_GRACE_SECONDS - 5) * 1000)
        await redis.set(b"audit:ws:first_empty:EMPTY_USDT", str(old).encode(), ex=86400)
        await redis.set(b"audit:ws:first_empty:STALE_USDT", str(old).encode(), ex=86400)
        await redis.set(b"audit:ws:first_empty:UNAPPR_USDT", str(old).encode(), ex=86400)
        n1 = await _evaluate_streaming_health(redis, report)
        return n1

    # Only EMPTY_USDT (ZCARD=0 + approved) should trigger.
    assert asyncio.run(_drive()) == 1


def test_remediator_marks_unverified_symbols_as_pending(monkeypatch):
    """Per-symbol verification gate: rowcount alone may not equal corrected."""
    from app.services import symbol_remediator as rem_mod

    captured = {}

    async def fake_bulk(db, symbols):
        captured["bulk"] = list(symbols)
        return len(captured["bulk"])

    async def fake_verify(db, symbols):
        # Only one of the two targets actually flipped to approved.
        return {"GOOD_USDT"}

    async def fake_refresh():
        return {"requested": True, "ts_ms": 1, "reason": "ok"}

    monkeypatch.setattr(rem_mod, "_bulk_approve", fake_bulk)
    monkeypatch.setattr(rem_mod, "_verify_approved", fake_verify)

    class _SessionCtx:
        async def __aenter__(self_inner): return self_inner
        async def __aexit__(self_inner, *a): return False
    monkeypatch.setattr(rem_mod, "AsyncSessionLocal", lambda: _SessionCtx(), raising=False)

    import sys as _sys
    db_mod = _sys.modules["app.database"]
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: _SessionCtx())

    leader_mod = _sys.modules["app.services.gate_ws_leader"]
    monkeypatch.setattr(leader_mod, "refresh_subscriptions", fake_refresh)

    report = _build_report(
        ("GOOD_USDT", STATUS_NOT_APPROVED),
        ("BAD_USDT", STATUS_NOT_APPROVED),
    )
    remediator = rem_mod.SymbolRemediator(
        validator=_FakeValidator(),
        recompute_indicators=False,
    )
    out = asyncio.run(remediator.remediate(report, dry_run=False))

    approve_actions = {a.symbol: a for a in out.actions if a.action == rem_mod.ACTION_APPROVE}
    assert approve_actions["GOOD_USDT"].executed is True
    assert approve_actions["GOOD_USDT"].error is None
    assert approve_actions["BAD_USDT"].executed is False
    assert "verification failed" in (approve_actions["BAD_USDT"].error or "")
