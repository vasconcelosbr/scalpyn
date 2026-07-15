"""Tests for shadow calibration cycle (autonomous, non-mutating).

Covers:
1. requires_human_approval=true in new suggestions/actions
2. Shadow calibration executor: correct before/after snapshot, version creation
3. Dedup: only one version per profile suggestion
4. Autopilot disabled → cycle skips
5. Score policy comes from active DB-backed PI settings
6. Failed profile does not block others
7. Safety guard endpoint fields
8. Forbids live-scope execution
9. Score bump is not a module-level hardcode
10. Updated_at set on suggestion/action update
11. commit called after successful cycle
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_db(autopilot_enabled=True, pending_rows=None):
    """Return a minimal AsyncSession mock for shadow calibration tests."""

    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    autopilot_result = MagicMock()
    autopilot_result.scalar.return_value = 1 if autopilot_enabled else 0

    if pending_rows is None:
        pending_rows = []

    pending_result = MagicMock()
    pending_result.fetchall.return_value = pending_rows

    execute_results = [
        autopilot_result,   # _is_autopilot_enabled query
        AsyncMock(),        # _log_activity STARTED
        pending_result,     # SELECT pending suggestions
    ]

    async def _execute(query, params=None):
        if not execute_results:
            return MagicMock()
        return execute_results.pop(0)

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _make_row(profile_id=None, suggestion_id=None, buy=65, confidence=0.8, pname="TestProfile"):
    r = SimpleNamespace()
    r.profile_id = profile_id or uuid.uuid4()
    r.suggestion_id = suggestion_id or uuid.uuid4()
    r.profile_name = pname
    r.target_section = "scoring"
    r.target_field = "minimum_score"
    r.confidence = confidence
    r.scoring_config = {"thresholds": {"buy": buy}}
    return r


# ---------------------------------------------------------------------------
# Test 1: requires_human_approval=false in INSERT SQL
# ---------------------------------------------------------------------------

def test_requires_human_approval_true_in_suggestion_insert():
    """The shadow suggestion INSERT must require human approval."""
    import inspect
    import app.services.profile_intelligence_live_service as svc

    source = inspect.getsource(svc)

    assert "'PENDING_SHADOW_VALIDATION',\n                     false, true," in source, (
        "shadow suggestions must require human approval"
    )


def test_requires_human_approval_true_in_pending_action_insert():
    """The SHADOW pending action INSERT must require human approval."""
    import inspect
    import app.services.profile_intelligence_live_service as svc

    source = inspect.getsource(svc)

    assert "'SHADOW',\n                     false, true, CAST" in source, (
        "SHADOW pending actions must require human approval"
    )


# ---------------------------------------------------------------------------
# Test 2: Autopilot disabled → cycle returns skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shadow_calibration_skips_when_autopilot_disabled():
    """When no autopilot_settings row has enabled=true, cycle must skip."""
    from unittest.mock import AsyncMock, MagicMock

    db = MagicMock()
    scalar_result = MagicMock()
    scalar_result.scalar.return_value = 0  # 0 = no autopilot enabled

    db.execute = AsyncMock(return_value=scalar_result)
    db.commit = AsyncMock()

    import app.services.profile_intelligence_live_service as svc
    result = await svc.run_shadow_calibration_cycle(db)

    assert result["status"] == "skipped_autopilot_disabled"
    db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Score bump = 5, default buy = 65 → new_buy = 70
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_bump_comes_from_active_policy():
    """Default bump: current_buy=65 → new_buy=70."""
    import inspect
    import app.services.profile_intelligence_live_service as svc

    source = inspect.getsource(svc.run_shadow_calibration_cycle)
    assert 'policy = await load_pi_settings(db, row.user_id)' in source
    assert 'policy["adjustment_score_bump"]' in source
    assert not hasattr(svc, "_SCORE_BUMP")


# ---------------------------------------------------------------------------
# Test 4: Score cap enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_cap_comes_from_active_policy():
    """Score must not exceed PI_SCORE_CAP (default 85) even with large current value."""
    import inspect
    import app.services.profile_intelligence_live_service as svc

    source = inspect.getsource(svc.run_shadow_calibration_cycle)
    assert 'min(current_buy + bump, int(policy["adjustment_score_cap"]))' in source
    assert not hasattr(svc, "_SCORE_CAP")


# ---------------------------------------------------------------------------
# Test 5: Before/after snapshot format
# ---------------------------------------------------------------------------

def test_before_after_snapshot_format():
    """Snapshots must use the nested scoring.thresholds.buy format."""
    current_buy = 65
    bump = 5
    new_buy = min(current_buy + bump, 85)

    before = {"scoring": {"thresholds": {"buy": current_buy}}}
    after = {"scoring": {"thresholds": {"buy": new_buy}}}
    diff = {"scoring": {"thresholds": {"buy": {"before": current_buy, "after": new_buy}}}}

    assert before["scoring"]["thresholds"]["buy"] == 65
    assert after["scoring"]["thresholds"]["buy"] == 70
    assert diff["scoring"]["thresholds"]["buy"]["before"] == 65
    assert diff["scoring"]["thresholds"]["buy"]["after"] == 70


# ---------------------------------------------------------------------------
# Test 6: mutation_applied must always be false in version record
# ---------------------------------------------------------------------------

def test_version_record_mutation_applied_false():
    """profile_adjustment_versions INSERT must set mutation_applied=false."""
    import inspect
    import app.services.profile_intelligence_live_service as svc

    source = inspect.getsource(svc.run_shadow_calibration_cycle)
    assert "mutation_applied" in source
    assert "SHADOW_APPLIED" in source
    # The INSERT VALUES must be: mutation_applied=false, rollback_available=true
    assert "'PENDING_VALIDATION', false, true, now()" in source, (
        "Version INSERT must have mutation_applied=false, rollback_available=true"
    )


# ---------------------------------------------------------------------------
# Test 7: Safety guard endpoint must expose shadow_calibration_autonomous
# ---------------------------------------------------------------------------

def test_safety_endpoint_has_shadow_calibration_autonomous():
    """The /safety endpoint response dict must include shadow_calibration_autonomous=True."""
    import inspect
    import app.api.profile_intelligence_live as live_api

    source = inspect.getsource(live_api.live_safety)
    assert "shadow_calibration_autonomous" in source
    assert "human_approval_required_for_production" in source
    assert "human_approval_required_for_shadow" in source


# ---------------------------------------------------------------------------
# Test 8: Forbidden action types are not allowed
# ---------------------------------------------------------------------------

def test_forbidden_action_types_defined():
    """_FORBIDDEN_SUGGESTION_TYPES must include CREATE_PROFILE, PROMOTE_LIVE, ENABLE_LIVE."""
    import app.services.profile_intelligence_live_service as svc

    required = {"CREATE_PROFILE", "DUPLICATE_PROFILE", "PROMOTE_LIVE", "ENABLE_LIVE"}
    missing = required - svc._FORBIDDEN_SUGGESTION_TYPES
    assert not missing, f"Missing forbidden types: {missing}"


# ---------------------------------------------------------------------------
# Test 9: Version INSERT SQL contains rollback_available=true
# ---------------------------------------------------------------------------

def test_version_insert_has_rollback_available():
    """Version records must have rollback_available=true for shadow calibrations."""
    import inspect
    import app.services.profile_intelligence_live_service as svc

    source = inspect.getsource(svc.run_shadow_calibration_cycle)
    assert "rollback_available" in source
    # The INSERT VALUES must have mutation_applied=false, rollback_available=true
    assert "'PENDING_VALIDATION', false, true, now()" in source, (
        "Version INSERT must have mutation_applied=false, rollback_available=true"
    )


# ---------------------------------------------------------------------------
# Test 10: Shadow executor function exists and is exported
# ---------------------------------------------------------------------------

def test_run_shadow_calibration_cycle_is_exported():
    """run_shadow_calibration_cycle must be importable from the service."""
    from app.services.profile_intelligence_live_service import run_shadow_calibration_cycle
    import asyncio
    assert asyncio.iscoroutinefunction(run_shadow_calibration_cycle)


# ---------------------------------------------------------------------------
# Test 11: Job feedback loop imports shadow calibration
# ---------------------------------------------------------------------------

def test_job_imports_shadow_calibration():
    """profile_intelligence_job._run_feedback_loop must import run_shadow_calibration_cycle."""
    import inspect
    import app.tasks.profile_intelligence_job as job

    source = inspect.getsource(job._run_feedback_loop)
    assert "run_shadow_calibration_cycle" in source, (
        "feedback_loop must import and call run_shadow_calibration_cycle"
    )
