import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import BackgroundTasks

from app.api.profile_intelligence import _queue_autopilot_cycle
from app.services.profile_intelligence_autopilot_service import (
    DEFAULT_AUTOPILOT_SETTINGS,
    ProfileIntelligenceAutopilotService,
    canonical_signature,
    evaluation_ready,
    promotion_decision,
    rollback_required,
    semantic_rules_equivalent,
)


@pytest.mark.asyncio
async def test_new_cycle_is_flushed_before_fk_audit():
    class _StopAfterOrderingCheck(Exception):
        pass

    class _FakeDB:
        def __init__(self):
            self.scalar_calls = 0
            self.flush_calls = 0

        async def scalar(self, *_args, **_kwargs):
            self.scalar_calls += 1
            return True if self.scalar_calls == 1 else None

        def add(self, _value):
            return None

        async def flush(self):
            self.flush_calls += 1

        async def execute(self, *_args, **_kwargs):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    class _Service(ProfileIntelligenceAutopilotService):
        async def get_settings(self, _db, _user_id):
            return SimpleNamespace(enabled=True), DEFAULT_AUTOPILOT_SETTINGS.copy()

        async def _audit(self, db, **_kwargs):
            assert db.flush_calls == 1
            raise _StopAfterOrderingCheck

    with pytest.raises(_StopAfterOrderingCheck):
        await _Service().run_cycle(_FakeDB(), uuid4(), analysis_run_id=uuid4())


@pytest.mark.asyncio
async def test_queue_autopilot_cycle_dispatches_structural_task(monkeypatch):
    dispatched = []

    def _delay(user_id, force):
        dispatched.append((user_id, force))
        return SimpleNamespace(id="task-123")

    fake_task = SimpleNamespace(delay=_delay)
    monkeypatch.setitem(
        sys.modules,
        "app.tasks.profile_intelligence_job",
        SimpleNamespace(run_for_user=fake_task),
    )
    user_id = uuid4()
    background_tasks = BackgroundTasks()

    result = await _queue_autopilot_cycle(background_tasks, user_id)

    assert result == {"cycle_status": "queued", "task_id": "task-123"}
    assert dispatched == [(str(user_id), False)]
    assert background_tasks.tasks == []


@pytest.mark.asyncio
async def test_queue_autopilot_cycle_falls_back_to_background_task(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise RuntimeError("broker unavailable")

    fake_task = SimpleNamespace(delay=_raise)
    monkeypatch.setitem(
        sys.modules,
        "app.tasks.profile_intelligence_job",
        SimpleNamespace(run_for_user=fake_task),
    )
    background_tasks = BackgroundTasks()

    result = await _queue_autopilot_cycle(background_tasks, uuid4())

    assert result == {"cycle_status": "queued", "task_id": None}
    assert len(background_tasks.tasks) == 1


def test_semantic_dedup_is_order_independent_and_uses_relative_tolerance():
    left = [
        {"field": "RSI", "operator": ">=", "value": 70},
        {"field": "ADX", "operator": ">", "value": 25},
    ]
    right = [
        {"indicator": "adx_14", "operator": "gt", "value": 27},
        {"indicator": "relative_strength_index", "operator": "gte", "value": 65},
    ]
    assert semantic_rules_equivalent(left, right, tolerance=0.20)
    assert canonical_signature(left) == canonical_signature(list(reversed(left)))
    assert canonical_signature([left[0]]) == canonical_signature([right[1]])


def test_semantic_dedup_preserves_indicator_operator_and_direction():
    base = [{"field": "rsi", "operator": ">=", "value": 70, "direction": "spot"}]
    assert not semantic_rules_equivalent(
        base,
        [{"field": "adx", "operator": ">=", "value": 70, "direction": "spot"}],
    )
    assert not semantic_rules_equivalent(
        base,
        [{"field": "rsi", "operator": "<=", "value": 70, "direction": "spot"}],
    )
    assert not semantic_rules_equivalent(
        base,
        [{"field": "rsi", "operator": ">=", "value": 70, "direction": "short"}],
    )


def test_review_window_requires_50_trades_after_36_hours():
    settings = DEFAULT_AUTOPILOT_SETTINGS
    assert not evaluation_ready(49, 100, settings)
    assert evaluation_ready(50, 36, settings)
    assert evaluation_ready(100, 1, settings)


def test_promotion_requires_both_minimums():
    settings = DEFAULT_AUTOPILOT_SETTINGS
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.80,
        avg_pnl_pct=0.005,
        settings=settings,
    )[0] == "APPROVE"
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.79,
        avg_pnl_pct=0.01,
        settings=settings,
    )[0] == "REJECT"
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.90,
        avg_pnl_pct=0.0049,
        settings=settings,
    )[0] == "REJECT"


def test_incumbent_must_improve_one_without_degrading_other():
    settings = DEFAULT_AUTOPILOT_SETTINGS
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.85,
        avg_pnl_pct=0.006,
        incumbent_exists=True,
        incumbent_win_rate=0.82,
        incumbent_avg_pnl_pct=0.006,
        settings=settings,
    )[0] == "APPROVE"
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.85,
        avg_pnl_pct=0.005,
        incumbent_exists=True,
        incumbent_win_rate=0.82,
        incumbent_avg_pnl_pct=0.006,
        settings=settings,
    )[0] == "REJECT"


def test_missing_metrics_never_causes_decision():
    decision, _ = promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=None,
        avg_pnl_pct=0.01,
        settings=DEFAULT_AUTOPILOT_SETTINGS,
    )
    assert decision == "INSUFFICIENT_EVIDENCE"
    incumbent_decision, _ = promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.90,
        avg_pnl_pct=0.01,
        incumbent_exists=True,
        incumbent_win_rate=None,
        incumbent_avg_pnl_pct=0.01,
        settings=DEFAULT_AUTOPILOT_SETTINGS,
    )
    assert incumbent_decision == "INSUFFICIENT_EVIDENCE"
    assert not rollback_required(None, 0.01, 0.80, 0.01, 0.80)


def test_rollback_on_either_metric_below_relative_floor():
    assert rollback_required(0.639, 0.01, 0.80, 0.01, 0.80)
    assert rollback_required(0.80, 0.0079, 0.80, 0.01, 0.80)
    assert not rollback_required(0.64, 0.008, 0.80, 0.01, 0.80)


class _ActionDB:
    def __init__(self):
        self.commit = AsyncMock()
        self.flush = AsyncMock()
        self.added = []

    def add(self, value):
        self.added.append(value)


def _candidate(state: str = "PENDING_HUMAN_APPROVAL"):
    user_id = uuid4()
    profile_id = uuid4()
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        cycle_id=uuid4(),
        profile_id=profile_id,
        origin_profile_id=None,
        previous_profile_id=uuid4(),
        shadow_watchlist_id=uuid4(),
        target_watchlist_id=uuid4(),
        state=state,
        approval_status="pending",
        approval_required=True,
        approved_by=None,
        approved_at=None,
        approval_reason=None,
        approval_source=None,
        approval_snapshot_json=None,
        promotion_blocked_reason=None,
        rollback_payload={"watchlist_id": str(uuid4())},
        live_activation_attempted_at=None,
        live_activated_at=None,
        observed_trades=100,
        observed_win_rate=0.85,
        observed_avg_pnl_pct=0.01,
        promotion_win_rate=None,
        promotion_avg_pnl_pct=None,
        evidence_json={},
        shadow_started_at=SimpleNamespace(),
        promoted_at=None,
        updated_at=None,
        rejected_at=None,
        rollback_at=None,
        decision_reason=None,
    )


@pytest.mark.asyncio
async def test_gates_pass_move_candidate_to_pending_human_approval(monkeypatch):
    service = ProfileIntelligenceAutopilotService()
    candidate = _candidate("SHADOW_READY")
    db = _ActionDB()
    live_watchlist = SimpleNamespace(id=uuid4(), profile_id=candidate.previous_profile_id, auto_refresh=True)
    candidate_profile = SimpleNamespace(
        id=candidate.profile_id,
        is_active=True,
        is_shadow_only=True,
        live_trading_enabled=False,
    )
    plan = {
        "live_watchlist": live_watchlist,
        "before_json": {"watchlist": {"profile_id": str(candidate.previous_profile_id)}},
        "after_json": {"watchlist": {"profile_id": str(candidate.profile_id)}},
        "diff_json": {"watchlist": {"before": {}, "after": {}}},
        "rollback_payload": {"previous_profile_id": str(candidate.previous_profile_id)},
    }
    audit = AsyncMock()
    monkeypatch.setattr(service, "_build_live_change_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "_audit", audit)

    await service._mark_pending_human_approval(
        db,
        candidate.user_id,
        SimpleNamespace(id=uuid4()),
        candidate,
        DEFAULT_AUTOPILOT_SETTINGS,
        {"trades": 100, "win_rate": 0.85, "avg_pnl_pct": 0.01},
        {"reasons": []},
        gates_passed=True,
    )

    assert candidate.state == "PENDING_HUMAN_APPROVAL"
    assert candidate_profile.live_trading_enabled is False
    assert live_watchlist.profile_id == candidate.previous_profile_id
    assert audit.await_args.kwargs["event_type"] == "LIVE_PROMOTION_BLOCKED_PENDING_APPROVAL"
    assert audit.await_args.kwargs["result"]["mutation_applied"] is False


@pytest.mark.asyncio
async def test_human_approval_does_not_activate_live(monkeypatch):
    service = ProfileIntelligenceAutopilotService()
    candidate = _candidate()
    db = _ActionDB()
    live_watchlist = SimpleNamespace(id=uuid4(), profile_id=candidate.previous_profile_id, auto_refresh=True)
    profile = SimpleNamespace(
        id=candidate.profile_id,
        is_active=True,
        is_shadow_only=True,
        live_trading_enabled=False,
    )
    plan = {
        "profile": profile,
        "live_watchlist": live_watchlist,
        "before_json": {},
        "after_json": {},
        "diff_json": {},
        "rollback_payload": {"watchlist_id": str(live_watchlist.id)},
    }
    monkeypatch.setattr(service, "_candidate_for_user", AsyncMock(return_value=candidate))
    monkeypatch.setattr(service, "_build_live_change_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "_audit", AsyncMock())

    result = await service.approve_candidate_for_live(
        db,
        candidate.user_id,
        candidate.id,
        approved_by=candidate.user_id,
        approval_reason="Métricas shadow revisadas e risco operacional aceito.",
        approval_source="test",
        confirm_risk=True,
    )

    assert result["state"] == "APPROVED_FOR_LIVE"
    assert candidate.approved_by == candidate.user_id
    assert candidate.approved_at is not None
    assert live_watchlist.profile_id == candidate.previous_profile_id
    assert profile.live_trading_enabled is False


@pytest.mark.asyncio
async def test_live_activation_requires_human_approval(monkeypatch):
    service = ProfileIntelligenceAutopilotService()
    candidate = _candidate("PENDING_HUMAN_APPROVAL")
    db = _ActionDB()
    monkeypatch.setattr(service, "_candidate_for_user", AsyncMock(return_value=candidate))
    monkeypatch.setattr(service, "_audit", AsyncMock())

    result = await service.activate_approved_candidate(
        db,
        candidate.user_id,
        candidate.id,
        activated_by=candidate.user_id,
    )

    assert result == {"status": "blocked", "reason": "missing_human_approval"}
    assert candidate.state == "PENDING_HUMAN_APPROVAL"


@pytest.mark.asyncio
async def test_blocked_approval_attempt_is_audited(monkeypatch):
    service = ProfileIntelligenceAutopilotService()
    candidate = _candidate()
    db = _ActionDB()
    audit = AsyncMock()
    monkeypatch.setattr(service, "_candidate_for_user", AsyncMock(return_value=candidate))
    monkeypatch.setattr(service, "_audit", audit)

    result = await service.approve_candidate_for_live(
        db,
        candidate.user_id,
        candidate.id,
        approved_by=candidate.user_id,
        approval_reason="Métricas revisadas, mas sem confirmação de risco.",
        approval_source="test",
        confirm_risk=False,
    )

    assert result == {"status": "blocked", "reason": "risk_confirmation_required"}
    assert audit.await_args.kwargs["event_type"] == "CANDIDATE_APPROVAL_BLOCKED"
    assert audit.await_args.kwargs["result"]["mutation_applied"] is False
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_human_approval_requires_sufficient_shadow_metrics(monkeypatch):
    service = ProfileIntelligenceAutopilotService()
    candidate = _candidate()
    candidate.observed_trades = 1
    candidate.observed_win_rate = None
    candidate.observed_avg_pnl_pct = None
    db = _ActionDB()
    audit = AsyncMock()
    monkeypatch.setattr(service, "_candidate_for_user", AsyncMock(return_value=candidate))
    monkeypatch.setattr(service, "_audit", audit)

    result = await service.approve_candidate_for_live(
        db,
        candidate.user_id,
        candidate.id,
        approved_by=candidate.user_id,
        approval_reason="Revisão humana solicitada para candidato sem amostra.",
        approval_source="test",
        confirm_risk=True,
    )

    assert result == {"status": "blocked", "reason": "insufficient_shadow_metrics"}
    assert audit.await_args.kwargs["result"]["mutation_applied"] is False


@pytest.mark.asyncio
async def test_activation_actor_must_match_authenticated_user(monkeypatch):
    service = ProfileIntelligenceAutopilotService()
    candidate = _candidate("APPROVED_FOR_LIVE")
    db = _ActionDB()
    audit = AsyncMock()
    monkeypatch.setattr(service, "_candidate_for_user", AsyncMock(return_value=candidate))
    monkeypatch.setattr(service, "_audit", audit)

    result = await service.activate_approved_candidate(
        db,
        candidate.user_id,
        candidate.id,
        activated_by=uuid4(),
    )

    assert result == {
        "status": "blocked",
        "reason": "activated_by_must_match_authenticated_user",
    }
    assert audit.await_args.kwargs["result"]["mutation_applied"] is False


@pytest.mark.asyncio
async def test_approved_candidate_activation_changes_live_association(monkeypatch):
    service = ProfileIntelligenceAutopilotService(
        gate_evaluator=SimpleNamespace(evaluate=AsyncMock(return_value=(True, {"reasons": []})))
    )
    candidate = _candidate("APPROVED_FOR_LIVE")
    candidate.approval_status = "approved"
    candidate.approved_by = candidate.user_id
    candidate.approved_at = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    candidate.approval_reason = "Aprovação humana explícita."
    candidate.rollback_payload = {"watchlist_id": str(candidate.target_watchlist_id)}
    profile = SimpleNamespace(
        id=candidate.profile_id,
        profile_version=None,
        is_active=True,
        is_shadow_only=True,
        live_trading_enabled=False,
    )
    incumbent = SimpleNamespace(id=candidate.previous_profile_id, live_trading_enabled=True)
    live_watchlist = SimpleNamespace(
        id=candidate.target_watchlist_id,
        profile_id=candidate.previous_profile_id,
        auto_refresh=True,
    )
    shadow = SimpleNamespace(id=candidate.shadow_watchlist_id, auto_refresh=True)
    plan = {
        "profile": profile,
        "incumbent": incumbent,
        "live_watchlist": live_watchlist,
        "shadow_watchlist": shadow,
        "before_json": {"watchlist": {"profile_id": str(candidate.previous_profile_id)}},
        "after_json": {"watchlist": {"profile_id": str(candidate.profile_id)}},
        "diff_json": {"watchlist": {"before": {}, "after": {}}},
    }
    db = _ActionDB()
    audit = AsyncMock()
    monkeypatch.setattr(service, "_candidate_for_user", AsyncMock(return_value=candidate))
    monkeypatch.setattr(service, "_build_live_change_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "_audit", audit)

    result = await service.activate_approved_candidate(
        db,
        candidate.user_id,
        candidate.id,
        activated_by=candidate.user_id,
    )

    assert result["state"] == "LIVE_ACTIVATED"
    assert live_watchlist.profile_id == candidate.profile_id
    assert profile.live_trading_enabled is True
    assert audit.await_args.kwargs["event_type"] == "LIVE_ACTIVATED"
    assert audit.await_args.kwargs["result"]["diff_json"]
    assert audit.await_args.kwargs["result"]["rollback_payload"]


@pytest.mark.asyncio
async def test_live_activation_fails_closed_when_operational_gate_is_unsafe(monkeypatch):
    service = ProfileIntelligenceAutopilotService(
        gate_evaluator=SimpleNamespace(evaluate=AsyncMock(return_value=(False, {"reasons": ["healthcheck_failed"]})))
    )
    candidate = _candidate("APPROVED_FOR_LIVE")
    candidate.approval_status = "approved"
    candidate.approved_by = candidate.user_id
    candidate.approved_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    candidate.approval_reason = "Aprovação humana explícita."
    candidate.rollback_payload = {"watchlist_id": str(candidate.target_watchlist_id)}
    db = _ActionDB()
    audit = AsyncMock()
    build_plan = AsyncMock()
    monkeypatch.setattr(service, "_candidate_for_user", AsyncMock(return_value=candidate))
    monkeypatch.setattr(service, "_build_live_change_plan", build_plan)
    monkeypatch.setattr(service, "_audit", audit)

    result = await service.activate_approved_candidate(
        db, candidate.user_id, candidate.id, activated_by=candidate.user_id,
    )

    assert result == {
        "status": "blocked",
        "reason": "operational_gates_failed",
        "gates": {"reasons": ["healthcheck_failed"]},
    }
    build_plan.assert_not_awaited()
    assert audit.await_args.kwargs["event_type"] == "LIVE_ACTIVATION_BLOCKED_OPERATIONAL_GATES"
    assert audit.await_args.kwargs["result"]["mutation_applied"] is False


@pytest.mark.asyncio
async def test_live_activation_without_rollback_is_blocked(monkeypatch):
    service = ProfileIntelligenceAutopilotService()
    candidate = _candidate("APPROVED_FOR_LIVE")
    candidate.approval_status = "approved"
    candidate.approved_by = candidate.user_id
    candidate.approved_at = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    candidate.approval_reason = "Aprovado."
    candidate.rollback_payload = None
    db = _ActionDB()
    monkeypatch.setattr(service, "_candidate_for_user", AsyncMock(return_value=candidate))
    monkeypatch.setattr(service, "_audit", AsyncMock())

    result = await service.activate_approved_candidate(
        db,
        candidate.user_id,
        candidate.id,
        activated_by=candidate.user_id,
    )

    assert result == {"status": "blocked", "reason": "missing_rollback_payload"}
    assert candidate.state == "APPROVED_FOR_LIVE"


@pytest.mark.asyncio
async def test_rejected_candidate_cannot_be_activated(monkeypatch):
    service = ProfileIntelligenceAutopilotService()
    candidate = _candidate("REJECTED")
    db = _ActionDB()
    monkeypatch.setattr(service, "_candidate_for_user", AsyncMock(return_value=candidate))
    monkeypatch.setattr(service, "_audit", AsyncMock())

    result = await service.activate_approved_candidate(
        db,
        candidate.user_id,
        candidate.id,
        activated_by=candidate.user_id,
    )

    assert result == {"status": "blocked", "reason": "candidate_rejected"}


@pytest.mark.asyncio
async def test_rollback_restores_incumbent_profile(monkeypatch):
    service = ProfileIntelligenceAutopilotService()
    candidate = _candidate("LIVE_ACTIVATED")
    incumbent_id = candidate.previous_profile_id
    target = SimpleNamespace(
        id=candidate.target_watchlist_id,
        profile_id=candidate.profile_id,
        auto_refresh=True,
    )
    profile = SimpleNamespace(
        id=candidate.profile_id,
        is_active=True,
        is_shadow_only=False,
        live_trading_enabled=True,
    )
    incumbent = SimpleNamespace(
        id=incumbent_id,
        is_active=False,
        is_shadow_only=True,
        live_trading_enabled=False,
    )
    shadow = SimpleNamespace(id=candidate.shadow_watchlist_id, auto_refresh=False)
    candidate.rollback_payload = {
        "watchlist_id": str(target.id),
        "previous_profile_id": str(incumbent_id),
        "watchlist_auto_refresh": True,
        "candidate_profile": {
            "is_active": True,
            "is_shadow_only": True,
            "live_trading_enabled": False,
        },
        "incumbent_profile": {
            "is_active": True,
            "is_shadow_only": False,
            "live_trading_enabled": True,
        },
    }
    objects = {
        target.id: target,
        profile.id: profile,
        incumbent.id: incumbent,
        shadow.id: shadow,
    }
    db = _ActionDB()
    db.get = AsyncMock(side_effect=lambda _model, object_id: objects.get(object_id))
    audit = AsyncMock()
    monkeypatch.setattr(service, "_audit", audit)

    await service._rollback_candidate(
        db,
        candidate.user_id,
        SimpleNamespace(id=uuid4()),
        candidate,
        DEFAULT_AUTOPILOT_SETTINGS,
        {"trigger": "test"},
        actor_user_id=candidate.user_id,
    )

    assert target.profile_id == incumbent_id
    assert candidate.state == "ROLLED_BACK"
    assert profile.live_trading_enabled is False
    assert incumbent.live_trading_enabled is True
    assert audit.await_args.kwargs["event_type"] == "CANDIDATE_ROLLED_BACK"


def test_pi_approval_migration_extends_phase1_without_parallel_head():
    backend_dir = Path(__file__).resolve().parents[1]
    phase1 = backend_dir / "alembic" / "versions" / "legacy" / "094_autopilot_scope_audit.py"
    phase2 = (
        backend_dir
        / "alembic"
        / "versions"
        / "legacy"
        / "095_pi_autopilot_human_live_approval.py"
    )
    assert phase1.is_file()
    assert phase2.is_file()

    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    scripts = ScriptDirectory.from_config(config)

    assert len(scripts.get_heads()) == 1
    phase1_source = phase1.read_text(encoding="utf-8")
    phase2_source = phase2.read_text(encoding="utf-8")
    assert 'revision = "094_autopilot_scope_audit"' in phase1_source
    assert 'down_revision = "093_pi_autopilot"' in phase1_source
    assert 'revision = "095_pi_human_live_approval"' in phase2_source
    assert 'down_revision = "094_autopilot_scope_audit"' in phase2_source
