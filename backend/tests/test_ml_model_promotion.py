from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from backend.app.api.ml import promote_ml_model


PROMOTION_CONFIG = {
    "ml_promotion_min_test_auc": 0.6,
    "ml_promotion_min_test_samples": 300,
    "ml_promotion_max_val_test_gap": 0.05,
    "ml_promotion_max_test_fpr": 0.5,
    "ml_promotion_require_positive_net_ev": True,
}


class _Result:
    def __init__(self, *, first=None, rows=None):
        self._first = first
        self._rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self._first

    def fetchall(self):
        return self._rows


def _model_row(*, test_samples=300, test_auc=0.63):
    at = datetime(2026, 7, 14, tzinfo=timezone.utc)
    return {
        "id": UUID("00000000-0000-0000-0000-000000000080"),
        "status": "candidate",
        "model_lane": "L3_PROFILE",
        "model_scope": "global",
        "profile_id": None,
        "label_version": "is_tp_4h_v2_sim_outcome",
        "source_filter": "L3",
        "dataset_contract_id": UUID("00000000-0000-0000-0000-000000000001"),
        "label_contract_id": UUID("00000000-0000-0000-0000-000000000002"),
        "feature_contract_id": UUID("00000000-0000-0000-0000-000000000003"),
        "feature_count": 35,
        "test_samples": test_samples,
        "roc_auc": 0.62,
        "metrics_json": {
            "validation": {"roc_auc": 0.62},
            "test": {
                "roc_auc": test_auc,
                "samples": test_samples,
                "fpr": 0.2,
                "net_ev": 0.1,
            },
        },
        "train_from": at,
        "train_to": at,
        "dataset_query_cutoff": at,
        "dataset_hash": "a" * 64,
        "predictive_status": None,
        "calibration_authority": False,
        "rule_generation_authority": False,
        "execution_authority": False,
    }


@pytest.mark.asyncio
async def test_promote_model_rejects_candidate_that_fails_gate(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Result(first=_model_row(test_samples=56, test_auc=0.35)),
        _Result(first=(PROMOTION_CONFIG,)),
        _Result(),
    ])
    audit = AsyncMock()
    monkeypatch.setattr(
        "backend.app.services.profile_intelligence_audit_service.log_pi_event",
        audit,
    )

    with pytest.raises(HTTPException) as exc:
        await promote_ml_model(
            UUID("00000000-0000-0000-0000-000000000080"),
            db=db,
            user_id=UUID("00000000-0000-0000-0000-000000000010"),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "MODEL_PROMOTION_GATE_NOT_APPROVED"
    assert db.commit.await_count == 1
    assert audit.await_count == 1


@pytest.mark.asyncio
async def test_promote_model_activates_approved_candidate_transactionally(monkeypatch):
    retired_id = UUID("00000000-0000-0000-0000-000000000070")
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Result(first=_model_row()),
        _Result(first=(PROMOTION_CONFIG,)),
        _Result(),
        _Result(rows=[(retired_id,)]),
        _Result(),
        _Result(),
        _Result(),
    ])
    audit = AsyncMock()
    monkeypatch.setattr(
        "backend.app.services.profile_intelligence_audit_service.log_pi_event",
        audit,
    )

    result = await promote_ml_model(
        UUID("00000000-0000-0000-0000-000000000080"),
        db=db,
        user_id=UUID("00000000-0000-0000-0000-000000000010"),
    )

    assert result["status"] == "active"
    assert result["promotion_gate"]["status"] == "APPROVED"
    assert result["retired_models"] == 1
    assert result["idempotent"] is False
    assert db.commit.await_count == 1
    assert audit.await_count == 1
