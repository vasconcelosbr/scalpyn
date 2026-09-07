from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import profiles as profiles_api
from app.services import profile_status_service
from app.services.profile_status_service import (
    ProfileStatusConflict,
    parse_expected_updated_at,
    validate_status_payload,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_status_payload_requires_boolean_reason_and_timezone():
    now = datetime.now(timezone.utc)
    assert validate_status_payload({
        "is_active": False,
        "reason": "performance review",
        "expected_updated_at": now.isoformat(),
    }) == (False, "performance review", now)

    with pytest.raises(ProfileStatusConflict, match="PROFILE_STATUS_REASON_REQUIRED"):
        validate_status_payload({
            "is_active": False,
            "reason": " ",
            "expected_updated_at": now.isoformat(),
        })
    with pytest.raises(ProfileStatusConflict, match="PROFILE_STATUS_BOOLEAN_REQUIRED"):
        validate_status_payload({
            "is_active": "false",
            "reason": "reason",
            "expected_updated_at": now.isoformat(),
        })
    with pytest.raises(ProfileStatusConflict, match="EXPECTED_UPDATED_AT_TIMEZONE_REQUIRED"):
        parse_expected_updated_at("2026-09-07T12:00:00")


class _ProfileResult:
    def __init__(self, profile):
        self.profile = profile

    def scalars(self):
        return self

    def first(self):
        return self.profile


class _Session:
    def __init__(self, profile):
        self.profile = profile

    async def execute(self, _statement, _params=None):
        return _ProfileResult(self.profile)


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def fetchall(self):
        return self.rows


class _StatusSession:
    def __init__(self, results):
        self.results = iter(results)
        self.added = []
        self.commits = 0
        self.flushes = 0

    async def execute(self, _statement, _params=None):
        return next(self.results)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None


@pytest.mark.asyncio
async def test_common_save_cannot_change_status():
    profile = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        profile_type="STANDARD",
        profile_role="acquisition_queue",
    )
    with pytest.raises(HTTPException) as caught:
        await profiles_api.update_profile(
            profile.id,
            {"is_active": False},
            db=_Session(profile),
            user_id=profile.user_id,
        )
    assert caught.value.status_code == 409
    assert caught.value.detail == {"code": "PROFILE_STATUS_ENDPOINT_REQUIRED"}


@pytest.mark.asyncio
async def test_l3_deactivation_preserves_config_version_and_association():
    now = datetime.now(timezone.utc)
    profile = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        name="L3",
        profile_role="acquisition_queue",
        profile_type="STANDARD",
        is_active=True,
        config={"default_timeframe": "5m", "custom": {"keep": True}},
        profile_version=now,
        updated_at=now,
    )
    watchlist = SimpleNamespace(
        id=uuid4(), name="L3 Momentum", level="L3", auto_refresh=True
    )
    original_config = profile.config.copy()
    session = _StatusSession([
        _RowsResult([profile]),
        _RowsResult([watchlist]),
        _RowsResult([SimpleNamespace(id=uuid4())]),
        _RowsResult([SimpleNamespace(id=uuid4())]),
    ])

    result = await profile_status_service.apply_profile_status(
        session,
        profile_id=profile.id,
        user_id=profile.user_id,
        payload={
            "is_active": False,
            "reason": "baixo desempenho observado",
            "expected_updated_at": now.isoformat(),
        },
    )

    assert result["changed"] is True
    assert result["open_trades_preserved"] is True
    assert profile.is_active is False
    assert profile.config == original_config
    assert profile.profile_version == now
    assert session.commits == 1
    assert len(result["watchlists"]) == 1
    assert len(result["cleared_live_watchlists"]) == 1
    audit = session.added[0]
    assert audit.previous_is_active is True
    assert audit.new_is_active is False
    assert audit.status_reason == "baixo desempenho observado"
    assert audit.previous_config is None
    assert audit.new_config is None


@pytest.mark.asyncio
async def test_upstream_deactivation_is_blocked_without_mutation():
    now = datetime.now(timezone.utc)
    profile = SimpleNamespace(
        id=uuid4(), user_id=uuid4(), name="L1", profile_role="primary_filter",
        profile_type="STANDARD", is_active=True, config={}, profile_version=now,
        updated_at=now,
    )
    watchlist = SimpleNamespace(
        id=uuid4(), name="L1", level="L1", auto_refresh=True
    )
    session = _StatusSession([_RowsResult([profile]), _RowsResult([watchlist])])

    with pytest.raises(ProfileStatusConflict, match="PROFILE_UPSTREAM_DEACTIVATION_BLOCKED"):
        await profile_status_service.apply_profile_status(
            session,
            profile_id=profile.id,
            user_id=profile.user_id,
            payload={
                "is_active": False,
                "reason": "tentativa protegida",
                "expected_updated_at": now.isoformat(),
            },
        )

    assert profile.is_active is True
    assert session.commits == 0
    assert session.added == []


@pytest.mark.asyncio
async def test_status_change_is_idempotent_even_with_stale_expected_timestamp():
    now = datetime.now(timezone.utc)
    profile = SimpleNamespace(
        id=uuid4(), user_id=uuid4(), name="L3", profile_role="acquisition_queue",
        profile_type="STANDARD", is_active=False, config={"keep": True},
        profile_version=now, updated_at=now,
    )
    session = _StatusSession([_RowsResult([profile]), _RowsResult([])])

    result = await profile_status_service.apply_profile_status(
        session,
        profile_id=profile.id,
        user_id=profile.user_id,
        payload={
            "is_active": False,
            "reason": "retry da mesma operação",
            "expected_updated_at": "2026-01-01T00:00:00+00:00",
        },
    )

    assert result["changed"] is False
    assert result["idempotent"] is True
    assert session.commits == 0
    assert session.added == []


@pytest.mark.asyncio
async def test_status_change_rejects_concurrent_update_before_mutation():
    now = datetime.now(timezone.utc)
    profile = SimpleNamespace(
        id=uuid4(), user_id=uuid4(), name="L3", profile_role="acquisition_queue",
        profile_type="STANDARD", is_active=True, config={"keep": True},
        profile_version=now, updated_at=now,
    )
    session = _StatusSession([_RowsResult([profile])])

    with pytest.raises(ProfileStatusConflict, match="PROFILE_STATUS_STALE"):
        await profile_status_service.apply_profile_status(
            session,
            profile_id=profile.id,
            user_id=profile.user_id,
            payload={
                "is_active": False,
                "reason": "tentativa concorrente",
                "expected_updated_at": "2026-01-01T00:00:00+00:00",
            },
        )

    assert profile.is_active is True
    assert session.commits == 0
    assert session.added == []


def test_status_service_preserves_rule_identity_and_blocks_upstream():
    source = inspect.getsource(profile_status_service)
    assert "PROFILE_UPSTREAM_DEACTIVATION_BLOCKED" in source
    assert "canonical_profile_config_hash(profile.config or {}) != config_hash_before" in source
    assert "profile.profile_version != profile_version_before" in source
    assert "previous_config=None" in source
    assert "new_config=None" in source


def test_no_status_only_path_versions_profile_rules():
    from app.services import governed_change_service

    source = inspect.getsource(governed_change_service)
    execution = source[source.index('if operation == "SET_PROFILE_ACTIVE_STATUS":', source.index("async def approve_and_execute")):]
    execution = execution[:execution.index('elif operation == "UPDATE_PROFILE_CONFIG_SET":')]
    rollback = source[source.index('if payload.get("operation_type") == "SET_PROFILE_ACTIVE_STATUS":', source.index("async def rollback")):]
    rollback = rollback[:rollback.index('elif payload.get("operation_type") == "UPDATE_PROFILE_CONFIG_SET":')]
    assert "profile_version =" not in execution
    assert "profile_version =" not in rollback


def test_every_live_pipeline_boundary_requires_active_profile():
    from app.api import watchlists
    from app.services import l3_authorization_outbox_service, pipeline_live_candidates
    from app.tasks import evaluate_signals, execute_buy, pipeline_scan

    watchlist_source = inspect.getsource(watchlists)
    live_source = inspect.getsource(pipeline_live_candidates)
    scan_source = inspect.getsource(pipeline_scan)
    evaluate_source = inspect.getsource(evaluate_signals)
    buy_source = inspect.getsource(execute_buy)
    outbox_source = inspect.getsource(l3_authorization_outbox_service)

    assert "PROFILE_INACTIVE_NOT_ASSOCIABLE" in watchlist_source
    assert "Profile.is_active.is_(True)" in live_source
    assert "_watchlist_profile_is_active" in scan_source
    assert "FOR UPDATE OF p" in scan_source
    assert 'reason="PROFILE_INACTIVE"' in evaluate_source
    assert "UserProfile.is_active.is_(True)" in evaluate_source
    assert 'reason="PROFILE_INACTIVE"' in buy_source
    assert 'processing_result = "PROFILE_INACTIVE"' in outbox_source


def test_open_shadow_monitor_does_not_depend_on_profile_active_state():
    source = (BACKEND_ROOT / "app/tasks/shadow_trade_monitor.py").read_text(encoding="utf-8")
    assert "from ..models.profile import Profile" not in source
    assert "select(Profile)" not in source
    assert 'ShadowTrade.status.in_(("PENDING", "RUNNING"))' in source


@pytest.mark.asyncio
async def test_inactive_l3_profile_cannot_create_a_new_shadow():
    from app.services.shadow_trade_service import _create_from_decision

    now = datetime.now(timezone.utc)
    decision = SimpleNamespace(
        id=123,
        user_id=uuid4(),
        symbol="BTC_USDT",
        strategy="profile-signal",
        direction="SPOT",
        created_at=now,
        metrics={},
        profile_id=uuid4(),
        profile_version=now,
        profile_name="L3 inactive",
    )
    db = AsyncMock()
    db.scalar.return_value = None
    config = {
        "tp_pct": 2.0,
        "sl_pct": 1.0,
        "amount_usdt": 100.0,
        "timeout_candles": 60,
        "ttt_enabled": False,
        "ttt_tp_pct": 1.0,
        "ttt_timeout_minutes": 180,
        "trailing": {"enabled": False},
    }

    created = await _create_from_decision(
        db, decision, "L3_AUTHORIZATION_OUTBOX_V3", config
    )

    assert created is None
    db.scalar.assert_awaited_once()
    db.execute.assert_not_awaited()


def test_status_migration_is_additive_and_reversible():
    source = (BACKEND_ROOT / "alembic/versions/221_profile_status_audit.py").read_text(encoding="utf-8")
    assert 'down_revision = "220_shadow_l3_closure_path"' in source
    assert 'sa.Column("previous_is_active", sa.Boolean(), nullable=True)' in source
    assert 'sa.Column("new_is_active", sa.Boolean(), nullable=True)' in source
    assert 'sa.Column("status_reason", sa.Text(), nullable=True)' in source
