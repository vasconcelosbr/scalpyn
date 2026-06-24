"""Tests for Label Lab (Profile Intelligence Adaptive Loop, Fase 5,
audit 2026-06-24).

Validates the pure-logic evaluator in
backend/app/services/profile_intelligence_label_lab.py — the check that
should have existed BEFORE v41/v42 were trained on is_tp_4h_v1 and collapsed
to test AUC 0.497 / 0.422.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.services.profile_intelligence_label_lab import (
    evaluate_label_candidate,
    is_viable,
    VIABLE,
    INSUFFICIENT_SAMPLES,
    DEGENERATE_CLASS_BALANCE,
    WIN_OUTCOME,
    LOSS_OUTCOME,
)


def _rows(n_win_fast, n_win_slow, n_loss, source="L1_SPECTRUM", profile_id="p1"):
    """n_win_fast: TP_HIT with holding_seconds well inside the window.
    n_win_slow: TP_HIT with holding_seconds way outside any reasonable window."""
    rows = []
    for _ in range(n_win_fast):
        rows.append({"outcome": WIN_OUTCOME, "holding_seconds": 600, "source": source, "profile_id": profile_id})
    for _ in range(n_win_slow):
        rows.append({"outcome": WIN_OUTCOME, "holding_seconds": 999999, "source": source, "profile_id": profile_id})
    for _ in range(n_loss):
        rows.append({"outcome": LOSS_OUTCOME, "holding_seconds": 1200, "source": source, "profile_id": profile_id})
    return rows


class TestInsufficientSamples:
    def test_few_samples_rejected_even_if_balanced(self):
        rows = _rows(n_win_fast=10, n_win_slow=0, n_loss=10)
        result = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
        )
        assert result["status"] == INSUFFICIENT_SAMPLES
        assert any("insufficient_samples" in r for r in result["reasons"])
        assert not is_viable(result)

    def test_exactly_at_threshold_is_not_insufficient(self):
        rows = _rows(n_win_fast=250, n_win_slow=0, n_loss=250)  # 500 total
        result = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
            min_total_samples=500,
        )
        assert result["metrics"]["total_samples"] == 500
        assert result["status"] != INSUFFICIENT_SAMPLES


class TestDegenerateClassBalance:
    def test_almost_all_losses_is_degenerate(self):
        rows = _rows(n_win_fast=10, n_win_slow=0, n_loss=990)
        result = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
        )
        assert result["status"] == DEGENERATE_CLASS_BALANCE
        assert any("degenerate_class_balance" in r for r in result["reasons"])

    def test_almost_all_wins_is_degenerate(self):
        rows = _rows(n_win_fast=990, n_win_slow=0, n_loss=10)
        result = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
        )
        assert result["status"] == DEGENERATE_CLASS_BALANCE


class TestViablePath:
    def test_balanced_large_sample_is_viable(self):
        rows = _rows(n_win_fast=400, n_win_slow=0, n_loss=600)
        result = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
        )
        assert result["status"] == VIABLE
        assert result["reasons"] == []
        assert is_viable(result)
        assert result["metrics"]["total_samples"] == 1000
        assert result["metrics"]["positive_count"] == 400


class TestWindowSemantics:
    def test_tp_hit_outside_window_counts_as_loss_not_win(self):
        """A TP_HIT that took too long to close is NOT a fast win — this is
        exactly the is_tp_4h_v1 vs is_win_fast_v1 distinction. A slow TP_HIT
        must reduce positive_rate relative to using holding_seconds<=window."""
        rows = _rows(n_win_fast=300, n_win_slow=300, n_loss=400)
        narrow = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
        )
        wide = evaluate_label_candidate(
            rows, label_version="is_tp_4h_v1", target_window_seconds=999999,
        )
        assert narrow["metrics"]["positive_count"] == 300
        assert wide["metrics"]["positive_count"] == 600
        assert narrow["metrics"]["total_samples"] == wide["metrics"]["total_samples"]

    def test_unlabelable_rows_excluded_not_guessed(self):
        rows = _rows(n_win_fast=300, n_win_slow=0, n_loss=300)
        rows.append({"outcome": None, "holding_seconds": None, "source": "L1_SPECTRUM", "profile_id": "p1"})
        rows.append({"outcome": "TP_HIT", "holding_seconds": None, "source": "L1_SPECTRUM", "profile_id": "p1"})
        result = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
        )
        assert result["metrics"]["total_samples"] == 600
        assert result["metrics"]["skipped_unlabelable"] == 2


class TestSourceFilter:
    def test_source_filter_excludes_other_sources(self):
        rows = _rows(n_win_fast=300, n_win_slow=0, n_loss=300, source="L1_SPECTRUM")
        rows += _rows(n_win_fast=300, n_win_slow=0, n_loss=300, source="L3")
        result = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
            source_filter=["L1_SPECTRUM"],
        )
        assert result["metrics"]["total_samples"] == 600
        assert result["by_source"] == {"L1_SPECTRUM": 600}

    def test_no_source_filter_includes_all(self):
        rows = _rows(n_win_fast=150, n_win_slow=0, n_loss=150, source="L1_SPECTRUM")
        rows += _rows(n_win_fast=150, n_win_slow=0, n_loss=150, source="L3")
        result = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
        )
        assert result["metrics"]["total_samples"] == 600
        assert result["by_source"] == {"L1_SPECTRUM": 300, "L3": 300}


class TestResultShape:
    def test_result_has_required_keys(self):
        rows = _rows(n_win_fast=300, n_win_slow=0, n_loss=300)
        result = evaluate_label_candidate(
            rows, label_version="is_win_fast_v1", target_window_seconds=1800,
        )
        for key in (
            "status", "evaluated_at", "reasons", "label_version",
            "target_window_seconds", "thresholds", "metrics", "by_source",
        ):
            assert key in result

    def test_empty_rows_is_insufficient_not_a_crash(self):
        result = evaluate_label_candidate(
            [], label_version="is_win_fast_v1", target_window_seconds=1800,
        )
        assert result["status"] == INSUFFICIENT_SAMPLES
        assert result["metrics"]["total_samples"] == 0
        assert result["metrics"]["positive_rate"] is None


class TestMigration107:
    def _migration_source(self) -> str:
        path = (
            Path(__file__).resolve().parents[2]
            / "backend" / "alembic" / "versions" / "107_label_lab_runs.py"
        )
        return path.read_text(encoding="utf-8")

    def test_revision_chain(self):
        source = self._migration_source()
        assert 'revision = "107_label_lab_runs"' in source
        assert 'down_revision = "106_shadow_ml_lineage"' in source
