from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.ml import native_capture_governance as governance


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Mappings(self._rows)


class _FakeDB:
    def __init__(self, pages):
        self._pages = iter(pages)
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if params is None:
            return _Result([])
        return _Result(next(self._pages))


def _row(created_at, row_id):
    return {
        "id": UUID(int=row_id),
        "source": "L3_REJECTED",
        "profile_id": None,
        "ranking_id": None,
        "decision_id": None,
        "profile_version_id": UUID(int=100 + row_id),
        "score_engine_version_id": UUID(int=200 + row_id),
        "features_snapshot": {"atr_pct": 1.0},
        "features_captured_at": created_at,
        "feature_hash": governance.snapshot_hash({"atr_pct": 1.0}),
        "feature_extractor_version": governance.EXTRACTOR_VERSION,
        "feature_schema_version": governance.SCHEMA_VERSION,
        "capture_contract_version": governance.CAPTURE_CONTRACT,
        "lineage_status": "EXACT",
        "eligible_for_training": True,
        "created_at": created_at,
        "completed_at": None,
    }


@pytest.mark.asyncio
async def test_full_window_uses_keyset_pages(monkeypatch):
    monkeypatch.setattr(governance, "FULL_WINDOW_BATCH_SIZE", 2)
    observed_at = datetime(2026, 7, 21, 18, 28, tzinfo=timezone.utc)
    rows = [_row(observed_at, 3), _row(observed_at, 2), _row(observed_at, 1)]
    db = _FakeDB([rows[:2], rows[2:]])

    result = await governance.audit_native_capture(
        db,
        datetime(2026, 7, 12, 18, 21, 57, tzinfo=timezone.utc),
        full_window=True,
        audit_query_cutoff=observed_at,
    )

    assert result["status"] == "VALID"
    assert result["total_native"] == 3
    assert result["official_native_eligible"] == 3
    assert "REPEATABLE READ, READ ONLY" in db.calls[0][0]
    assert db.calls[1][1]["page_limit"] == 2
    assert db.calls[2][1]["cursor_created_at"] == observed_at
    assert db.calls[2][1]["cursor_id"] == UUID(int=2)


@pytest.mark.asyncio
async def test_canary_stops_after_one_bounded_query():
    observed_at = datetime(2026, 7, 21, 18, 28, tzinfo=timezone.utc)
    db = _FakeDB([[_row(observed_at, 1)]])

    result = await governance.audit_native_capture(
        db,
        datetime(2026, 7, 12, 18, 21, 57, tzinfo=timezone.utc),
        limit=999,
        full_window=False,
        audit_query_cutoff=observed_at,
    )

    assert result["total_native"] == 1
    assert db.calls[1][1]["page_limit"] == governance.CANARY_LIMIT
    assert len(db.calls) == 2


def test_precomputed_hash_match_preserves_full_window_semantics():
    observed_at = datetime(2026, 7, 21, 18, 28, tzinfo=timezone.utc)
    row = _row(observed_at, 1)
    row["feature_hash"] = "not-the-reduced-snapshot-hash"
    row["_feature_hash_matches"] = True

    assert "hash_mismatch" not in governance.official_row_errors(
        row,
        datetime(2026, 7, 12, 18, 21, 57, tzinfo=timezone.utc),
        reference_now=observed_at,
    )

    row["_feature_hash_matches"] = False
    assert "hash_mismatch" in governance.official_row_errors(
        row,
        datetime(2026, 7, 12, 18, 21, 57, tzinfo=timezone.utc),
        reference_now=observed_at,
    )
