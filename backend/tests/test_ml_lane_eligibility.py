"""Tests for ML model lane eligibility (audit P2-5 fix, 2026-06-24).

Validates:
  - WinFastPredictor.predict() / _get_threshold() accept model_lane explicitly.
  - get_model() / invalidate_model_cache() accept model_lane explicitly.
  - NoEligibleModelError exists and is distinct from a generic load failure.
  - predict() with no eligible model for a lane returns the
    NO_ELIGIBLE_MODEL_FOR_LANE contract (no probability, no random fallback).
  - Invalid model_lane values are rejected early (fail fast, not silently).

These are signature/contract tests (mirroring backend/tests/test_strategy_lab.py
style) — they don't require a live DB connection; predict()'s DB-dependent path
is exercised via mocked get_model()/NoEligibleModelError.
"""

import inspect
from unittest.mock import AsyncMock, patch

import pytest


def test_get_model_accepts_model_lane():
    from backend.app.ml.gcs_model_loader import get_model

    sig = inspect.signature(get_model)
    assert "model_lane" in sig.parameters
    assert sig.parameters["model_lane"].default is None


def test_loader_get_model_method_accepts_model_lane():
    from backend.app.ml.gcs_model_loader import GCSModelLoader

    sig = inspect.signature(GCSModelLoader.get_model)
    assert "model_lane" in sig.parameters


def test_load_from_db_accepts_model_lane():
    from backend.app.ml.gcs_model_loader import GCSModelLoader

    sig = inspect.signature(GCSModelLoader._load_from_db)
    assert "model_lane" in sig.parameters


def test_no_eligible_model_error_exists_and_is_exception():
    from backend.app.ml.gcs_model_loader import NoEligibleModelError

    assert issubclass(NoEligibleModelError, Exception)


def test_no_eligible_model_error_distinct_from_file_not_found():
    """Must NOT be a subclass of FileNotFoundError — callers need to tell apart
    'no eligible model for this lane' (expected, gate-driven) from 'model
    storage/infra is broken' (unexpected, generic fail-closed)."""
    from backend.app.ml.gcs_model_loader import NoEligibleModelError

    assert not issubclass(NoEligibleModelError, FileNotFoundError)


def test_invalidate_invalidates_all_lane_entries_for_profile():
    """invalidate(profile_id=X) must clear every cached lane entry for that
    profile, since cache_key is now 'profile:{id}:{lane}', not 'profile:{id}'."""
    from backend.app.ml.gcs_model_loader import GCSModelLoader

    loader = GCSModelLoader()
    loader._cache["profile:abc:L1_SPECTRUM"] = {"model": object()}
    loader._cache["profile:abc:L3_PROFILE"] = {"model": object()}
    loader._cache["profile:other:L1_SPECTRUM"] = {"model": object()}

    loader.invalidate(profile_id="abc")

    assert "profile:abc:L1_SPECTRUM" not in loader._cache
    assert "profile:abc:L3_PROFILE" not in loader._cache
    assert "profile:other:L1_SPECTRUM" in loader._cache  # untouched
    loader._cache.clear()  # cleanup singleton state for other tests


class TestPredictorModelLaneContract:
    def test_predict_accepts_model_lane(self):
        from backend.app.ml.prediction_service import WinFastPredictor

        sig = inspect.signature(WinFastPredictor.predict)
        assert "model_lane" in sig.parameters
        assert sig.parameters["model_lane"].default is None

    def test_get_threshold_accepts_model_lane(self):
        from backend.app.ml.prediction_service import WinFastPredictor

        sig = inspect.signature(WinFastPredictor._get_threshold)
        assert "model_lane" in sig.parameters

    def test_invalid_model_lane_raises_value_error(self):
        from backend.app.ml.prediction_service import WinFastPredictor

        predictor = WinFastPredictor()
        with pytest.raises(ValueError):
            import asyncio
            asyncio.run(predictor.predict(metrics={}, db=AsyncMock(), model_lane="NOT_A_REAL_LANE"))

    def test_no_eligible_model_returns_skipped_contract_not_random_score(self):
        """Rule #15 (absolute): no eligible model for a lane must never produce
        a fabricated probability — must return score_status=SKIPPED with the
        exact reason_code, and must NOT call the model at all.

        Note: NoEligibleModelError must be imported via the bare `app.` path
        (not `backend.app.`) because prediction_service.py itself imports it
        that way — importing the same source file under two different dotted
        paths creates two distinct class objects in sys.modules, and the
        `except NoEligibleModelError` clause inside predict() only matches the
        `app.` one. This mirrors how the module is actually loaded in
        production (PYTHONPATH=/app inside the container, no `backend.` prefix
        ever used there) — the `backend.` prefix is a test-harness-only path.
        """
        import asyncio

        from backend.app.ml.prediction_service import WinFastPredictor
        from app.ml.gcs_model_loader import NoEligibleModelError

        predictor = WinFastPredictor()
        with patch(
            "backend.app.ml.prediction_service.get_model",
            side_effect=NoEligibleModelError("no model for lane"),
        ):
            result = asyncio.run(predictor.predict(
                metrics={"rsi": 50.0}, db=AsyncMock(), model_lane="L1_SPECTRUM"
            ))

        assert result["win_fast_probability"] is None
        assert result["model_id"] is None
        assert result["model_approved"] is False
        assert result["score_status"] == "SKIPPED"
        assert result["reason_code"] == "NO_ELIGIBLE_MODEL_FOR_LANE"
        assert result["model_lane"] == "L1_SPECTRUM"

    def test_generic_model_load_failure_keeps_fail_closed_contract(self):
        """A real infra failure (not NoEligibleModelError) must keep the
        pre-existing fail-closed contract — model_approved=False — unchanged."""
        import asyncio

        from backend.app.ml.prediction_service import WinFastPredictor

        predictor = WinFastPredictor()
        with patch(
            "backend.app.ml.prediction_service.get_model",
            side_effect=RuntimeError("DB connection lost"),
        ):
            result = asyncio.run(predictor.predict(
                metrics={"rsi": 50.0}, db=AsyncMock(), model_lane="L3_PROFILE"
            ))

        assert result["model_approved"] is False
        assert result["score_status"] == "SKIPPED"
        assert result["reason_code"] == "model_unavailable_fail_closed"
