from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import autopilot_engine as engine


class _Db:
    def __init__(self, execute_results=None):
        self.execute = AsyncMock(side_effect=list(execute_results or []))
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


def _perf(*, user_id: str, profile_id: str, count: int = 30) -> dict:
    return {
        "approved_ev": -0.5,
        "approved_gross_ev": -0.4,
        "approved_win_rate": 0.4,
        "approved_count": count,
        "n_tp": 12,
        "n_sl": 18,
        "fpr": 0.6,
        "span_days": 10.0,
        "rejected_ev": 0.0,
        "rejected_win_rate": 0.0,
        "rejected_count": 0,
        "selection_inversion": 0.0,
        "analysis_days": 30,
        "autopilot_source": "L1_SPECTRUM",
        "computed_at": "2026-06-19T12:00:00+00:00",
        "user_id": user_id,
        "profile_id": profile_id,
        "evidence_count": count,
        "performance_window": {
            "start": "2026-05-20T12:00:00+00:00",
            "end": "2026-06-19T12:00:00+00:00",
            "source": "L1_SPECTRUM",
            "closed_trades": count,
            "profile_id": profile_id,
            "user_id": user_id,
        },
    }


@pytest.mark.asyncio
async def test_blocks_mutation_without_user_id(monkeypatch):
    profile_id = str(uuid4())
    audit = AsyncMock()
    monkeypatch.setattr(engine, "log_audit", audit)
    db = _Db()

    result = await engine.run_autopilot_cycle(
        profile_id=profile_id,
        profile_role="primary_filter",
        user_id=None,
        current_config={},
        auto_pilot_config={},
        db=db,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "missing_user_id"
    assert result["mutation_applied"] is False
    assert result["autopilot_still_active"] is True
    assert audit.await_args.kwargs["action"] == "AUTOPILOT_SCOPE_BLOCKED"


@pytest.mark.asyncio
async def test_blocks_mutation_without_profile_id(monkeypatch):
    user_id = str(uuid4())
    audit = AsyncMock()
    monkeypatch.setattr(engine, "log_audit", audit)
    db = _Db()

    result = await engine.run_autopilot_cycle(
        profile_id=None,
        profile_role="primary_filter",
        user_id=user_id,
        current_config={},
        auto_pilot_config={},
        db=db,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "missing_profile_id"
    assert result["mutation_applied"] is False
    assert audit.await_args.kwargs["action"] == "AUTOPILOT_SCOPE_BLOCKED"


@pytest.mark.asyncio
async def test_scoped_sample_never_falls_back_to_global(monkeypatch):
    user_id = str(uuid4())
    profile_id = str(uuid4())
    audit = AsyncMock()
    monkeypatch.setattr(engine, "_validate_mutation_scope", AsyncMock(return_value=None))
    monkeypatch.setattr(
        engine,
        "_load_guardrails",
        AsyncMock(
            return_value={
                "dry_run_mode": False,
                "kill_switch": False,
                "scope_profile_id": None,
                "min_scoped_closed_trades": 30,
            }
        ),
    )
    compute = AsyncMock(return_value=_perf(
        user_id=user_id,
        profile_id=profile_id,
        count=17,
    ))
    monkeypatch.setattr(engine, "compute_performance_window", compute)
    monkeypatch.setattr(engine, "log_audit", audit)
    db = _Db()

    result = await engine.run_autopilot_cycle(
        profile_id=profile_id,
        profile_role="primary_filter",
        user_id=user_id,
        current_config={},
        auto_pilot_config={},
        db=db,
    )

    assert result["reason"] == "insufficient_scoped_sample"
    assert result["closed_trades"] == 17
    assert result["min_required"] == 30
    assert result["mutation_applied"] is False
    compute.assert_awaited_once_with(
        days=engine.PERFORMANCE_DAYS,
        db=db,
        user_id=user_id,
        profile_id=profile_id,
        mutation_context=True,
    )


@pytest.mark.asyncio
async def test_performance_query_filters_user_and_profile(monkeypatch):
    user_id = str(uuid4())
    profile_a = str(uuid4())
    profile_b = str(uuid4())
    monkeypatch.setattr(engine, "_load_ml_fee_pct", AsyncMock(return_value=0.1))

    mapping_result = SimpleNamespace(
        mappings=lambda: SimpleNamespace(
            one=lambda: {
                "n": 30,
                "ev": 0.2,
                "gross_ev": 0.3,
                "win_rate": 0.6,
                "n_sl": 12,
                "n_tp": 18,
                "span_days": 9.0,
            }
        )
    )
    db = _Db([mapping_result])

    result = await engine.compute_performance_window(
        days=30,
        db=db,
        user_id=user_id,
        profile_id=profile_a,
        mutation_context=True,
    )

    statement, params = db.execute.await_args.args
    sql = str(statement)
    assert "user_id = CAST(:uid AS uuid)" in sql
    assert "profile_id = CAST(:profile_id AS uuid)" in sql
    assert params["uid"] == user_id
    assert params["profile_id"] == profile_a
    assert params["profile_id"] != profile_b
    assert result["performance_window"]["profile_id"] == profile_a


@pytest.mark.asyncio
async def test_valid_scoped_mutation_is_atomic_and_audited(monkeypatch):
    user_id = str(uuid4())
    profile_id = str(uuid4())
    version_id = str(uuid4())
    perf = _perf(user_id=user_id, profile_id=profile_id, count=30)
    audit = AsyncMock()

    monkeypatch.setattr(engine, "_validate_mutation_scope", AsyncMock(return_value=None))
    monkeypatch.setattr(
        engine,
        "_load_guardrails",
        AsyncMock(
            return_value={
                "dry_run_mode": False,
                "kill_switch": False,
                "scope_profile_id": None,
                "min_scoped_closed_trades": 30,
                "autopilot_full_authority": False,
                "autopilot_can_adjust": ["scoring_rules"],
            }
        ),
    )
    monkeypatch.setattr(engine, "compute_performance_window", AsyncMock(return_value=perf))
    monkeypatch.setattr(engine, "detect_regime", AsyncMock(return_value="SIDEWAYS"))
    monkeypatch.setattr(
        "app.services.skill_selector.select_skill_for_regime",
        AsyncMock(side_effect=RuntimeError("skip skill selection in unit test")),
    )
    monkeypatch.setattr(
        engine,
        "check_behavior_circuit_breaker",
        AsyncMock(return_value=(False, "disabled")),
    )
    monkeypatch.setattr(engine, "check_performance_rollback", lambda **_: (False, "disabled"))
    monkeypatch.setattr(engine, "_check_regression", lambda *_: 0)
    full_adjustments = AsyncMock(
        return_value={"scoring_rules": {"action": "RULES_ANALYZED"}}
    )
    monkeypatch.setattr(
        engine,
        "apply_full_adjustments",
        full_adjustments,
    )
    monkeypatch.setattr(engine, "should_mutate", lambda *_, **__: (True, "negative_ev"))
    monkeypatch.setattr(
        engine,
        "generate_mutated_config",
        AsyncMock(
            return_value={
                "config": {"minimum_score": 51},
                "regime": "SIDEWAYS",
                "analysis_summary": "scoped",
            }
        ),
    )
    monkeypatch.setattr(engine, "save_profile_version", AsyncMock(return_value=version_id))
    monkeypatch.setattr(engine, "log_audit", audit)

    update_result = SimpleNamespace(scalar_one_or_none=lambda: profile_id)
    db = _Db([update_result])

    result = await engine.run_autopilot_cycle(
        profile_id=profile_id,
        profile_role="primary_filter",
        user_id=user_id,
        current_config={"minimum_score": 50},
        auto_pilot_config={},
        db=db,
    )

    assert result["action"] == "MUTATED"
    assert result["mutation_applied"] is True
    full_adjustments.assert_not_awaited()
    assert result["rule_adjustment"]["reason"] == (
        "target_config_profiles_not_profile_scoped"
    )
    update_sql = str(db.execute.await_args_list[-1].args[0])
    update_params = db.execute.await_args_list[-1].args[1]
    assert "UPDATE profiles" in update_sql
    assert update_params["user_id"] == user_id
    assert update_params["profile_id"] == profile_id

    mutated_call = next(
        call for call in audit.await_args_list
        if call.kwargs.get("action") == "MUTATED"
    )
    assert mutated_call.kwargs["user_id"] == user_id
    assert mutated_call.kwargs["profile_id"] == profile_id
    assert mutated_call.kwargs["config_before"] == {"minimum_score": 50}
    assert mutated_call.kwargs["config_after"] == {"minimum_score": 51}
    assert mutated_call.kwargs["diff_json"]
    assert mutated_call.kwargs["mutation_applied"] is True
    blocked_global_call = next(
        call for call in audit.await_args_list
        if call.kwargs.get("action")
        == "AUTOPILOT_MUTATION_BLOCKED_INVALID_TARGET_SCOPE"
    )
    assert blocked_global_call.kwargs["target_config"] == "config_profiles"
    assert blocked_global_call.kwargs["mutation_applied"] is False


@pytest.mark.asyncio
async def test_mutated_audit_rejects_missing_payload():
    user_id = str(uuid4())
    profile_id = str(uuid4())
    db = _Db()

    with pytest.raises(engine.AutopilotScopeError, match="missing mutation audit payload"):
        await engine.log_audit(
            profile_id=profile_id,
            user_id=user_id,
            action="MUTATED",
            reason="test",
            reason_code="test",
            regime="SIDEWAYS",
            perf=_perf(user_id=user_id, profile_id=profile_id),
            db=db,
            config_before=None,
            config_after={"x": 2},
            diff_json={"x": {"before": 1, "after": 2}},
            mutation_applied=True,
        )

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_mutated_audit_persists_complete_payload():
    user_id = str(uuid4())
    profile_id = str(uuid4())
    before = {"minimum_score": 50}
    after = {"minimum_score": 51}
    diff = {"minimum_score": {"before": 50, "after": 51}}
    db = _Db()
    db.execute.side_effect = None
    db.execute.return_value = SimpleNamespace()

    await engine.log_audit(
        profile_id=profile_id,
        user_id=user_id,
        action="MUTATED",
        reason="negative_ev",
        reason_code="scoped_profile_mutation",
        regime="SIDEWAYS",
        perf=_perf(user_id=user_id, profile_id=profile_id),
        db=db,
        target_config=profile_id,
        target_section="profile_config",
        config_before=before,
        config_after=after,
        diff_json=diff,
        mutation_applied=True,
    )

    _, params = db.execute.await_args.args
    assert params["uid"] == user_id
    assert params["pid"] == profile_id
    assert params["performance_window"] is not None
    assert params["evidence_count"] == 30
    assert params["before"] is not None
    assert params["after"] is not None
    assert params["diff"] is not None
    assert params["mutation_applied"] is True
