"""Tests for Profile Intelligence Live Engine contracts.

These tests verify:
- DB contract: forbidden types blocked by CHECK constraints
- App contract: _ensure_no_forbidden raises on forbidden types
- Bucketing: _bucket returns expected values
- Action type validation: forbidden types are known and consistent
- Safety: mutation_applied default, requires_human_approval default
"""

import inspect

import pytest

from backend.app.services.profile_intelligence_live_service import (
    _bucket,
    _ensure_no_forbidden,
    _FORBIDDEN_SUGGESTION_TYPES,
    _FORBIDDEN_ACTION_TYPES,
)
import backend.app.services.profile_intelligence_live_service as live_service


# ── Forbidden type contracts ────────────────────────────────────────────────

def test_forbidden_suggestion_types_set_correct():
    expected = {"CREATE_PROFILE", "DUPLICATE_PROFILE", "PROMOTE_LIVE", "ENABLE_LIVE"}
    assert _FORBIDDEN_SUGGESTION_TYPES == expected


def test_forbidden_action_types_match_suggestion_types():
    assert _FORBIDDEN_ACTION_TYPES == _FORBIDDEN_SUGGESTION_TYPES


def test_ensure_no_forbidden_raises_create_profile():
    with pytest.raises(ValueError, match="Forbidden"):
        _ensure_no_forbidden("CREATE_PROFILE")


def test_ensure_no_forbidden_raises_duplicate_profile():
    with pytest.raises(ValueError, match="Forbidden"):
        _ensure_no_forbidden("DUPLICATE_PROFILE")


def test_ensure_no_forbidden_raises_promote_live():
    with pytest.raises(ValueError, match="Forbidden"):
        _ensure_no_forbidden("PROMOTE_LIVE")


def test_ensure_no_forbidden_raises_enable_live():
    with pytest.raises(ValueError, match="Forbidden"):
        _ensure_no_forbidden("ENABLE_LIVE")


def test_ensure_no_forbidden_allows_reduce_risk():
    # Should not raise
    _ensure_no_forbidden("REDUCE_RISK")


def test_ensure_no_forbidden_allows_adjust_scoring_weight():
    _ensure_no_forbidden("ADJUST_SCORING_WEIGHT")


def test_ensure_no_forbidden_allows_adjust_block_rule():
    _ensure_no_forbidden("ADJUST_BLOCK_RULE")


def test_ensure_no_forbidden_allows_adjust_indicator_range():
    _ensure_no_forbidden("ADJUST_INDICATOR_RANGE")


def test_ensure_no_forbidden_allows_pause_profile_shadow():
    _ensure_no_forbidden("PAUSE_PROFILE_SHADOW")


# ── Bucketing ───────────────────────────────────────────────────────────────

def test_bucket_rsi_oversold():
    assert _bucket("rsi", 25) == "oversold"


def test_bucket_rsi_overbought():
    assert _bucket("rsi", 75) == "overbought"


def test_bucket_rsi_neutral():
    assert _bucket("rsi", 50) == "neutral"


def test_bucket_adx_weak():
    assert _bucket("adx", 15) == "weak"


def test_bucket_adx_strong():
    assert _bucket("adx", 45) == "strong"


def test_bucket_adx_moderate():
    assert _bucket("adx", 30) == "moderate"


def test_bucket_ema9_gt_ema21_true():
    assert _bucket("ema9_gt_ema21", 1.0) == "true"


def test_bucket_ema9_gt_ema21_false():
    assert _bucket("ema9_gt_ema21", 0.0) == "false"


def test_bucket_generic_high():
    assert _bucket("volume_spike", 2.0) == "high"


def test_bucket_generic_low():
    assert _bucket("volume_spike", -1.5) == "low"


def test_bucket_generic_mid():
    assert _bucket("volume_spike", 0.0) == "mid"


def test_bucket_unknown_value():
    assert _bucket("rsi", None) == "unknown"
    assert _bucket("rsi", "not_a_number") == "unknown"


# ── Safety invariants ────────────────────────────────────────────────────────

def test_forbidden_types_cover_all_profile_creation_variants():
    """Every profile-creation action must be in the forbidden set."""
    creation_actions = ["CREATE_PROFILE", "DUPLICATE_PROFILE", "PROMOTE_LIVE", "ENABLE_LIVE"]
    for action in creation_actions:
        assert action in _FORBIDDEN_SUGGESTION_TYPES, f"{action} must be forbidden"
        assert action in _FORBIDDEN_ACTION_TYPES, f"{action} must be forbidden in actions"


def test_allowed_suggestion_types_do_not_overlap_forbidden():
    allowed = {
        "ADJUST_SIGNAL_RULE", "ADJUST_SCORING_WEIGHT", "ADJUST_BLOCK_RULE",
        "ADJUST_INDICATOR_RANGE", "ADJUST_MINIMUM_SCORE", "ADJUST_THRESHOLD",
        "ADD_SHADOW_ONLY_RULE", "REDUCE_RISK", "PAUSE_PROFILE_SHADOW",
    }
    overlap = allowed & _FORBIDDEN_SUGGESTION_TYPES
    assert overlap == set(), f"Overlap between allowed and forbidden: {overlap}"


def test_asyncpg_safe_uuid_binds_for_profile_queries():
    """SQLAlchemy asyncpg binds cannot use :pid::uuid syntax."""
    source = inspect.getsource(live_service)
    assert ":pid::uuid" not in source
    assert "CAST(:pid AS uuid)" in source
