"""Tests for the Profile Intelligence Feedback Engine over profile_suggestions
(Fase 11, audit 2026-06-24).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.services.profile_suggestion_feedback_engine import (
    evaluate_suggestion_feedback,
    resolve_profile_id_for_suggestion,
    no_profile_linked_result,
    PROMOTE_CANDIDATE,
    INSUFFICIENT_EVIDENCE,
    POOR_PERFORMANCE,
    NO_PROFILE_LINKED,
    WIN_OUTCOME,
    LOSS_OUTCOME,
)


def _rows(n_win, n_loss, n_other=0):
    rows = [{"outcome": WIN_OUTCOME} for _ in range(n_win)]
    rows += [{"outcome": LOSS_OUTCOME} for _ in range(n_loss)]
    rows += [{"outcome": "TIMEOUT"} for _ in range(n_other)]
    return rows


class TestResolveProfileId:
    def test_prefers_created_profile_id(self):
        row = {"profile_id": "orig", "created_profile_id": "created"}
        assert resolve_profile_id_for_suggestion(row) == "created"

    def test_falls_back_to_profile_id(self):
        row = {"profile_id": "orig", "created_profile_id": None}
        assert resolve_profile_id_for_suggestion(row) == "orig"

    def test_none_when_both_missing(self):
        row = {"profile_id": None, "created_profile_id": None}
        assert resolve_profile_id_for_suggestion(row) is None

    def test_none_when_keys_absent(self):
        assert resolve_profile_id_for_suggestion({}) is None


class TestEvaluateSuggestionFeedback:
    def test_insufficient_evidence_below_min_trades(self):
        result = evaluate_suggestion_feedback(_rows(n_win=10, n_loss=10))
        assert result["status"] == INSUFFICIENT_EVIDENCE
        assert result["metrics"]["trades"] == 20

    def test_poor_performance_low_win_rate_with_enough_trades(self):
        result = evaluate_suggestion_feedback(_rows(n_win=10, n_loss=40))
        assert result["metrics"]["trades"] == 50
        assert result["status"] == POOR_PERFORMANCE
        assert any("win_rate_below_threshold" in r for r in result["reasons"])

    def test_promote_candidate_good_win_rate_enough_trades(self):
        result = evaluate_suggestion_feedback(_rows(n_win=30, n_loss=20))
        assert result["metrics"]["trades"] == 50
        assert result["metrics"]["win_rate"] == 0.6
        assert result["status"] == PROMOTE_CANDIDATE
        assert result["reasons"] == []

    def test_timeout_and_none_outcomes_excluded_not_counted(self):
        result = evaluate_suggestion_feedback(_rows(n_win=30, n_loss=20, n_other=15))
        assert result["metrics"]["trades"] == 50  # the 15 TIMEOUTs are excluded

    def test_zero_trades_is_insufficient_not_a_crash(self):
        result = evaluate_suggestion_feedback([])
        assert result["status"] == INSUFFICIENT_EVIDENCE
        assert result["metrics"]["win_rate"] is None

    def test_custom_thresholds_respected(self):
        result = evaluate_suggestion_feedback(
            _rows(n_win=5, n_loss=5), min_closed_trades=5, min_win_rate=0.6,
        )
        assert result["metrics"]["trades"] == 10
        assert result["status"] == POOR_PERFORMANCE  # win_rate=0.5 < 0.6


class TestNoProfileLinkedResult:
    def test_shape_and_status(self):
        result = no_profile_linked_result()
        assert result["status"] == NO_PROFILE_LINKED
        assert result["metrics"]["trades"] == 0
        assert result["metrics"]["win_rate"] is None
        assert "evaluated_at" in result


class TestMigration108:
    def _migration_source(self) -> str:
        path = (
            Path(__file__).resolve().parents[2]
            / "backend" / "alembic" / "versions" / "108_suggestion_shadow_feedback.py"
        )
        return path.read_text(encoding="utf-8")

    def test_revision_chain(self):
        source = self._migration_source()
        assert 'revision = "108_suggestion_feedback"' in source
        assert 'down_revision = "107_label_lab_runs"' in source

    def test_adds_expected_columns(self):
        source = self._migration_source()
        assert '"shadow_feedback_status"' in source
        assert '"shadow_feedback_json"' in source
