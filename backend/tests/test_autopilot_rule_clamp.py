"""Unit tests for adjust_rule_points — L-04 regression guard.

Critical invariant: pts=40 + positive_edge MUST NOT collapse to 10.
The old clamp `min(current + 1, 10)` would do this when current > 10.
"""
from __future__ import annotations

import pytest
from app.services.autopilot_engine import adjust_rule_points


def _rule(rule_id: str, pts: int) -> dict:
    return {"id": rule_id, "indicator": "rsi", "operator": "<=", "value": 25, "points": pts}


def _insights(rule_id: str, *, n: int = 30, win_rate: float, overall_wr: float) -> dict:
    return {
        "_overall": {"win_rate": overall_wr, "n": 100},
        rule_id: {
            "n": n,
            "win_rate": win_rate,
            "ev": (win_rate - overall_wr) * 0.02,
            "edge": win_rate - overall_wr,
        },
    }


class TestOutOfRangeSkip:
    """Rules outside [rule_points_min, rule_points_max] must be skipped, never clamped."""

    def test_pts_40_positive_edge_skipped_not_collapsed(self):
        rules = [_rule("r1", 40)]
        ins = _insights("r1", win_rate=0.70, overall_wr=0.50)  # edge=+0.20 > threshold
        adjusted, n_changed, changes = adjust_rule_points(
            rules, ins, rule_points_min=-10, rule_points_max=10, rule_max_delta=1
        )
        assert n_changed == 0, "rule with pts=40 (> max=10) must be skipped, not adjusted"
        assert adjusted[0]["points"] == 40, "pts=40 must be preserved exactly"

    def test_pts_40_result_never_10(self):
        rules = [_rule("r1", 40)]
        ins = _insights("r1", win_rate=0.70, overall_wr=0.50)
        adjusted, _, _ = adjust_rule_points(
            rules, ins, rule_points_min=-10, rule_points_max=10, rule_max_delta=1
        )
        assert adjusted[0]["points"] != 10, "destructive clamp pts=40→10 must never happen"

    def test_pts_30_positive_edge_skipped(self):
        rules = [_rule("r1", 30)]
        ins = _insights("r1", win_rate=0.75, overall_wr=0.50)  # edge=+0.25
        adjusted, n_changed, _ = adjust_rule_points(
            rules, ins, rule_points_min=-10, rule_points_max=10, rule_max_delta=1
        )
        assert n_changed == 0
        assert adjusted[0]["points"] == 30

    def test_pts_negative_15_skipped(self):
        rules = [_rule("r1", -15)]
        ins = _insights("r1", win_rate=0.20, overall_wr=0.50)  # edge=-0.30 < -threshold
        adjusted, n_changed, _ = adjust_rule_points(
            rules, ins, rule_points_min=-10, rule_points_max=10, rule_max_delta=1
        )
        assert n_changed == 0, "rule with pts=-15 (< min=-10) must be skipped"
        assert adjusted[0]["points"] == -15


class TestWithinRangeAdjust:
    """Rules inside range must be adjusted normally with proper clamp."""

    def test_pts_5_positive_edge_increments_by_1(self):
        rules = [_rule("r1", 5)]
        ins = _insights("r1", win_rate=0.70, overall_wr=0.50)  # edge=+0.20
        adjusted, n_changed, changes = adjust_rule_points(
            rules, ins, rule_points_min=-10, rule_points_max=10, rule_max_delta=1
        )
        assert n_changed == 1
        assert adjusted[0]["points"] == 6

    def test_pts_10_positive_edge_clamped_at_max(self):
        rules = [_rule("r1", 10)]
        ins = _insights("r1", win_rate=0.70, overall_wr=0.50)  # edge=+0.20
        adjusted, n_changed, _ = adjust_rule_points(
            rules, ins, rule_points_min=-10, rule_points_max=10, rule_max_delta=1
        )
        assert n_changed == 0, "already at max — no change expected"
        assert adjusted[0]["points"] == 10

    def test_pts_negative_5_negative_edge_decrements(self):
        rules = [_rule("r1", -5)]
        ins = _insights("r1", win_rate=0.20, overall_wr=0.50)  # edge=-0.30
        adjusted, n_changed, _ = adjust_rule_points(
            rules, ins, rule_points_min=-10, rule_points_max=10, rule_max_delta=1
        )
        assert n_changed == 1
        assert adjusted[0]["points"] == -6

    def test_pts_0_no_significant_edge_unchanged(self):
        rules = [_rule("r1", 0)]
        ins = _insights("r1", win_rate=0.52, overall_wr=0.50)  # edge=+0.02 < threshold 0.10
        adjusted, n_changed, _ = adjust_rule_points(
            rules, ins, rule_points_min=-10, rule_points_max=10, rule_max_delta=1
        )
        assert n_changed == 0
        assert adjusted[0]["points"] == 0

    def test_guardrail_bounds_respected_from_params(self):
        rules = [_rule("r1", 8)]
        ins = _insights("r1", win_rate=0.70, overall_wr=0.50)
        adjusted, n_changed, _ = adjust_rule_points(
            rules, ins, rule_points_min=-5, rule_points_max=8, rule_max_delta=1
        )
        assert n_changed == 0, "already at custom max=8 — no change"
        assert adjusted[0]["points"] == 8


class TestInsufficientSamples:
    """Rules with insufficient sample count must be skipped."""

    def test_low_n_skipped(self):
        rules = [_rule("r1", 5)]
        ins = _insights("r1", n=5, win_rate=0.90, overall_wr=0.50)  # edge=+0.40 but n=5
        adjusted, n_changed, _ = adjust_rule_points(rules, ins)
        assert n_changed == 0
        assert adjusted[0]["points"] == 5
