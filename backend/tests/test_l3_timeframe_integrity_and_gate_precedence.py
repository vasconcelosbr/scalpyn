"""AUD-001/AUD-002 (auditoria shadow SL_HIT 2026-09-11): regression coverage
for the two confirmed, correctable findings.

AUD-002: an indicator merged from conflicting timeframes (e.g. bb_width
resolved from a 30m row when the profile's default_timeframe is 5m) must be
visible in decision_audit even though it is not yet enforced (see
profile_engine._build_eval_data for why blanking it out would make entry
*more* permissive, not less, given BlockEngine/SignalEngine's SKIPPED
semantics).

AUD-001: a profile with both entry_triggers and signals populated must warn
at save time, since entry_triggers silently shadows signals entirely.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.profile_engine import ProfileEngine, indicator_timeframe_conflicts
from app.services.profile_config_validation import profile_config_warnings
from app.utils.indicator_merge import merge_indicator_rows


def _profile(**overrides):
    base = {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "scoring": {"enabled": True, "weights": {}, "rules": [], "selected_rule_ids": []},
        "signals": {"logic": "AND", "conditions": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
    }
    base.update(overrides)
    return base


def test_indicator_timeframe_conflicts_reports_conflicting_key():
    """Reproduces the T04/T06 shape: bb_width merged from a 30m row winning
    over a 5m row under the same 'structural' scheduler group."""
    now = datetime(2026, 9, 11, 7, 20, tzinfo=timezone.utc)
    merged = merge_indicator_rows([
        ("structural", now - timedelta(minutes=25), "30m", {"bb_width": 0.054002}),
        ("structural", now - timedelta(minutes=2), "5m", {"bb_width": 0.02254}),
    ], now=now)
    asset = {
        "symbol": "NEAR_USDT",
        "indicators": merged.as_flat_dict(),
        "_merged_indicators": merged,
    }
    conflicts = indicator_timeframe_conflicts(asset)
    assert "bb_width" in conflicts
    assert conflicts["bb_width"]["observed_timeframes"] == ["30m", "5m"]


def test_indicator_timeframe_conflicts_empty_when_no_merged_indicators():
    assert indicator_timeframe_conflicts({"indicators": {"rsi": 50}}) == {}
    assert indicator_timeframe_conflicts({}) == {}


def test_build_eval_data_surfaces_conflicts_without_changing_values():
    """Fase 1 is observability-only: the value ProfileEngine evaluates
    conditions against must not change just because a conflict is detected."""
    now = datetime(2026, 9, 11, 7, 20, tzinfo=timezone.utc)
    merged = merge_indicator_rows([
        ("structural", now - timedelta(minutes=25), "30m", {"bb_width": 0.054002}),
        ("structural", now - timedelta(minutes=2), "5m", {"bb_width": 0.02254}),
    ], now=now)
    asset = {
        "symbol": "NEAR_USDT",
        "indicators": merged.as_flat_dict(),
        "_merged_indicators": merged,
    }
    engine = ProfileEngine(_profile())
    eval_data = engine._build_eval_data(asset)
    assert eval_data["bb_width"] == merged.values["bb_width"]  # unchanged
    assert "bb_width" in eval_data["_timeframe_conflicts"]


def test_profile_config_warnings_flags_entry_triggers_shadowing_signals():
    """Reproduces T00/T01: signals.momentum_score>=56 never runs because
    entry_triggers has its own, unrelated conditions."""
    config = _profile(
        entry_triggers={"logic": "AND", "conditions": [
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.55, "enabled": True, "required": True},
        ]},
        signals={"logic": "AND", "conditions": [
            {"field": "momentum_score", "operator": ">=", "value": 56},
        ]},
    )
    warnings = profile_config_warnings(config)
    assert len(warnings) == 1
    assert "entry_triggers" in warnings[0] and "signals" in warnings[0]


def test_profile_config_warnings_silent_when_only_one_section_populated():
    only_entry_triggers = _profile(
        entry_triggers={"logic": "AND", "conditions": [
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.55, "enabled": True, "required": True},
        ]},
    )
    assert profile_config_warnings(only_entry_triggers) == []

    only_signals = _profile(
        signals={"logic": "AND", "conditions": [
            {"field": "momentum_score", "operator": ">=", "value": 56},
        ]},
    )
    assert profile_config_warnings(only_signals) == []

    neither = _profile()
    assert profile_config_warnings(neither) == []
