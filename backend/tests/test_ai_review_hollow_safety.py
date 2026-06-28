from datetime import datetime, timezone
from pathlib import Path

from app.services.ai_review_safety_service import (
    completed_review_contract_is_valid,
    is_hollow_completed_review,
    reclassified_status,
)

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 6, 28, tzinfo=timezone.utc)


def test_safety_fails_on_completed_zero_tokens():
    assert is_hollow_completed_review(status="COMPLETED", tokens_input=0,
                                      tokens_output=0, summary="")


def test_safety_passes_on_legacy_hollow_review():
    assert not is_hollow_completed_review(status="LEGACY_HOLLOW_REVIEW", tokens_input=0,
                                          tokens_output=0, summary=None)


def test_safety_passes_on_failed_empty_ai_response():
    assert not is_hollow_completed_review(status="FAILED_EMPTY_AI_RESPONSE", tokens_input=0,
                                          tokens_output=0, summary=None)


def test_reclassification_snapshots_hollow_reviews():
    source = (ROOT / "backend/scripts/reclassify_hollow_ai_reviews.py").read_text(encoding="utf-8")
    assert "to_jsonb(r) AS snapshot" in source
    assert "profile_ai_review_reclassification_audit" in source
    assert "review_snapshot" in source


def test_reclassification_does_not_delete_reviews():
    source = (ROOT / "backend/scripts/reclassify_hollow_ai_reviews.py").read_text(encoding="utf-8").upper()
    assert "DELETE FROM PROFILE_AI_REVIEWS" not in source
    assert "UPDATE PROFILE_AI_REVIEWS" in source


def test_completed_requires_tokens_and_summary():
    valid = dict(status="COMPLETED", tokens_input=10, tokens_output=5,
                 summary="real", model_name="claude", completed_at=NOW)
    assert completed_review_contract_is_valid(**valid)
    for missing in ("tokens_input", "tokens_output", "summary", "model_name", "completed_at"):
        case = valid.copy()
        case[missing] = None
        assert not completed_review_contract_is_valid(**case)


def test_calibration_evolution_safety_displays_warning_not_blocker_for_legacy():
    source = (ROOT / "frontend/app/profile-intelligence/page.tsx").read_text(encoding="utf-8")
    assert "Safety Guard —" in source
    assert "legacy_hollow_reviews_24h" in source
    assert "Aviso informativo:" in source
    assert "calSafety.safety_pass" in source


def test_no_new_hollow_ai_review_after_once_run():
    service = (ROOT / "backend/app/services/profile_intelligence_live_service.py").read_text(encoding="utf-8")
    migration = (ROOT / "backend/alembic/versions/116_ai_review_hollow_safety.py").read_text(encoding="utf-8")
    assert "completed_review_contract_is_valid" in service
    assert "completed_at = :completed_at" in service
    assert "enforce_completed_ai_review_contract" in migration
    assert "RAISE EXCEPTION" in migration


def test_reclassification_uses_fix_deploy_boundary():
    before = datetime(2026, 6, 27, 15, tzinfo=timezone.utc)
    after = datetime(2026, 6, 27, 16, tzinfo=timezone.utc)
    fix = datetime(2026, 6, 27, 15, 30, tzinfo=timezone.utc)
    assert reclassified_status(requested_at=before, created_at=before,
                               fix_deployed_at=fix) == "LEGACY_HOLLOW_REVIEW"
    assert reclassified_status(requested_at=after, created_at=after,
                               fix_deployed_at=fix) == "FAILED_EMPTY_AI_RESPONSE"
