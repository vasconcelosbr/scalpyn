"""Tests for shadow_trade_duplicate_resolver (audit 2026-06-24, item 4 of
the post-VALIDACAO_GERAL punch list — decision_id duplicate fix, audit-only,
no DELETE).
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.services.shadow_trade_duplicate_resolver import resolve_duplicate_group


def _row(id_, created_at, outcome=None):
    return {"id": id_, "created_at": created_at, "outcome": outcome}


T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


class TestCanonicalSelection:
    def test_earliest_created_at_is_canonical(self):
        rows = [
            _row("b", T0 + timedelta(minutes=5)),
            _row("a", T0),
            _row("c", T0 + timedelta(minutes=10)),
        ]
        result = resolve_duplicate_group(rows)
        assert result["canonical_id"] == "a"
        assert set(result["superseded_ids"]) == {"b", "c"}

    def test_tie_broken_by_id_string(self):
        rows = [_row("z", T0), _row("a", T0)]
        result = resolve_duplicate_group(rows)
        assert result["canonical_id"] == "a"
        assert result["superseded_ids"] == ["z"]

    def test_resolution_reason_recorded(self):
        rows = [_row("a", T0), _row("b", T0 + timedelta(seconds=1))]
        result = resolve_duplicate_group(rows)
        assert result["resolution_reason"] == "earliest_created_at"


class TestConflictDetection:
    def test_same_outcome_no_conflict(self):
        rows = [_row("a", T0, "TP_HIT"), _row("b", T0 + timedelta(1), "TP_HIT")]
        result = resolve_duplicate_group(rows)
        assert result["conflict"] is False
        assert result["distinct_outcomes_count"] == 1

    def test_different_outcomes_is_conflict(self):
        rows = [_row("a", T0, "TP_HIT"), _row("b", T0 + timedelta(1), "SL_HIT")]
        result = resolve_duplicate_group(rows)
        assert result["conflict"] is True
        assert result["distinct_outcomes_count"] == 2

    def test_null_outcomes_excluded_from_distinct_count(self):
        rows = [_row("a", T0, None), _row("b", T0 + timedelta(1), "TP_HIT")]
        result = resolve_duplicate_group(rows)
        assert result["conflict"] is False
        assert result["distinct_outcomes_count"] == 1

    def test_all_null_outcomes_no_conflict(self):
        rows = [_row("a", T0, None), _row("b", T0 + timedelta(1), None)]
        result = resolve_duplicate_group(rows)
        assert result["conflict"] is False
        assert result["distinct_outcomes_count"] == 0

    def test_outcomes_map_includes_every_row(self):
        rows = [_row("a", T0, "TP_HIT"), _row("b", T0 + timedelta(1), "SL_HIT")]
        result = resolve_duplicate_group(rows)
        assert result["outcomes"] == {"a": "TP_HIT", "b": "SL_HIT"}

    def test_conflict_never_silently_resolved_to_a_guessed_outcome(self):
        """The function must never invent a 'winning' outcome — conflict=True
        is the only signal; outcomes map preserves both raw values."""
        rows = [_row("a", T0, "TP_HIT"), _row("b", T0 + timedelta(1), "SL_HIT")]
        result = resolve_duplicate_group(rows)
        assert "resolved_outcome" not in result
        assert "outcome" not in result


class TestEdgeCases:
    def test_single_row_is_its_own_canonical(self):
        result = resolve_duplicate_group([_row("a", T0, "TP_HIT")])
        assert result["canonical_id"] == "a"
        assert result["superseded_ids"] == []
        assert result["conflict"] is False

    def test_empty_rows_raises(self):
        import pytest
        with pytest.raises(ValueError):
            resolve_duplicate_group([])

    def test_three_way_duplicate_with_one_conflicting_outcome(self):
        rows = [
            _row("a", T0, "TP_HIT"),
            _row("b", T0 + timedelta(1), "TP_HIT"),
            _row("c", T0 + timedelta(2), "SL_HIT"),
        ]
        result = resolve_duplicate_group(rows)
        assert result["canonical_id"] == "a"
        assert set(result["superseded_ids"]) == {"b", "c"}
        assert result["conflict"] is True
        assert result["distinct_outcomes_count"] == 2
