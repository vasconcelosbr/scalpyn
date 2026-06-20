"""Regression coverage for profile-aware decision-log deduplication."""

from pathlib import Path


PIPELINE_SCAN = (
    Path(__file__).resolve().parents[1] / "app" / "tasks" / "pipeline_scan.py"
)


def test_decision_dedup_is_scoped_by_user_and_profile():
    source = PIPELINE_SCAN.read_text(encoding="utf-8")
    start = source.index("async def _persist_decision_logs")
    end = source.index("\n\nasync def ", start + 1)
    function_source = source[start:end]

    assert "DecisionLog.user_id == user_id" in function_source
    assert "decision.get(\"_profile_id\")" in function_source
    assert "DecisionLog.profile_id == profile_id" in function_source
    assert "DecisionLog.profile_id.is_(None)" in function_source


def test_existing_decision_key_includes_profile_id():
    source = PIPELINE_SCAN.read_text(encoding="utf-8")
    start = source.index("async def _persist_decision_logs")
    end = source.index("\n\nasync def ", start + 1)
    function_source = source[start:end]

    assert "row.profile_id" in function_source
