"""Regression test for the fail-open cache bug found during Última Fase
Fase 1 (audit 2026-06-25).

Bug: when no APPROVED model exists for a model_lane, get_model() correctly
raises NoEligibleModelError on the first call (cache miss). But the
negative result was cached as {"model": None} with no record of *why* —
so every subsequent call within MODEL_CACHE_TTL seconds silently returned
None instead of re-raising. Callers (prediction_service.WinFastPredictor)
then crashed deeper in model.predict_proba(None), an AttributeError caught
by a generic except in pipeline_scan.py's _ml_predict_one whose fallback
is model_approved=True — i.e. fail-OPEN, not fail-closed, for every call
except the very first one per TTL window.

Fix: store the exception instance alongside the negative cache entry and
re-raise it on every cache hit while the entry is None, so the gate fails
closed consistently regardless of which call actually hit the database.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")

from app.ml.gcs_model_loader import GCSModelLoader, NoEligibleModelError  # noqa: E402


def _make_fake_connect(fetchone_return=None):
    """Builds a fake psycopg2.connect(...) returning no eligible row,
    matching the `with conn.cursor() as cur: ... cur.fetchone()` shape
    used by GCSModelLoader._load_from_db."""
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_return
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.close.return_value = None
    return MagicMock(return_value=conn)


@pytest.fixture
def loader():
    """Fresh, isolated cache_key per test — the loader itself is a
    process-wide singleton (intentional, mirrors production), so tests
    must not share cache_key with each other or with real callers."""
    instance = GCSModelLoader()
    instance._cache.pop("global:L3_PROFILE_TEST", None)
    yield instance
    instance._cache.pop("global:L3_PROFILE_TEST", None)


class TestNoEligibleModelCacheFailsClosedOnEveryCall:
    def test_first_call_raises_no_eligible_model_error(self, loader, monkeypatch):
        monkeypatch.setattr(
            "app.ml.gcs_model_loader.psycopg2.connect",
            _make_fake_connect(fetchone_return=None),
        )
        with pytest.raises(NoEligibleModelError):
            loader.get_model(model_lane="L3_PROFILE_TEST")

    def test_second_call_within_ttl_also_raises_not_none(self, loader, monkeypatch):
        """The actual regression: before the fix, this second call (cache
        HIT on the negative entry) returned None silently instead of
        re-raising — the fail-open bug."""
        monkeypatch.setattr(
            "app.ml.gcs_model_loader.psycopg2.connect",
            _make_fake_connect(fetchone_return=None),
        )
        with pytest.raises(NoEligibleModelError):
            loader.get_model(model_lane="L3_PROFILE_TEST")

        # Second call must NOT hit the DB again (still within TTL) and
        # must NOT return None silently — it must re-raise.
        connect_mock = MagicMock(side_effect=AssertionError("must not query DB again within TTL"))
        monkeypatch.setattr("app.ml.gcs_model_loader.psycopg2.connect", connect_mock)
        with pytest.raises(NoEligibleModelError):
            loader.get_model(model_lane="L3_PROFILE_TEST")

    def test_cached_error_is_the_same_exception_type_raised_originally(self, loader, monkeypatch):
        monkeypatch.setattr(
            "app.ml.gcs_model_loader.psycopg2.connect",
            _make_fake_connect(fetchone_return=None),
        )
        with pytest.raises(NoEligibleModelError):
            loader.get_model(model_lane="L3_PROFILE_TEST")

        cache_key = "global:L3_PROFILE_TEST"
        assert loader._cache[cache_key]["model"] is None
        assert isinstance(loader._cache[cache_key]["error"], NoEligibleModelError)


class TestSuccessPathUnaffected:
    def test_successful_load_has_no_error_key_and_returns_model(self, loader, monkeypatch):
        import joblib
        import io

        fake_model = {"model": "FAKE_MODEL_OBJECT", "feature_columns": ["a", "b"]}
        buf = io.BytesIO()
        joblib.dump(fake_model, buf)
        blob = buf.getvalue()

        monkeypatch.setattr(
            "app.ml.gcs_model_loader.psycopg2.connect",
            _make_fake_connect(fetchone_return=(blob, "99")),
        )
        result = loader.get_model(model_lane="L3_PROFILE_TEST")
        assert result == "FAKE_MODEL_OBJECT"

        cache_key = "global:L3_PROFILE_TEST"
        assert "error" not in loader._cache[cache_key] or loader._cache[cache_key].get("error") is None
