"""Validation and persistence policy for calibration evidence."""

from __future__ import annotations

import json
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .model_governance import can_publish_ml_evidence


REQUIRED_EVIDENCE_FIELDS = {
    "source_version",
    "dataset_hash",
    "window_from",
    "window_to",
    "target_path",
    "indicator",
    "operator",
    "ci95_lower",
    "ci95_upper",
    "raw_n",
    "effective_n",
    "independent_windows",
    "symbols",
    "confidence",
}


def validate_evidence(payload: Mapping[str, Any]) -> list[str]:
    missing = sorted(key for key in REQUIRED_EVIDENCE_FIELDS if payload.get(key) is None)
    errors = [f"missing:{key}" for key in missing]
    if payload.get("operator") in {"between", "range"} and (
        payload.get("lower") is None or payload.get("upper") is None
    ):
        errors.append("range_requires_lower_and_upper")
    if payload.get("raw_n") is not None and payload.get("effective_n") is not None:
        if float(payload["effective_n"]) > int(payload["raw_n"]):
            errors.append("effective_n_exceeds_raw_n")
    if payload.get("target_path") is not None and not str(payload["target_path"]).startswith("/"):
        errors.append("target_path_must_be_absolute")
    if payload.get("ci95_lower") is not None and payload.get("ci95_upper") is not None:
        if float(payload["ci95_lower"]) > float(payload["ci95_upper"]):
            errors.append("invalid_confidence_interval")
    if payload.get("confidence") is not None:
        confidence = float(payload["confidence"])
        if confidence < 0 or confidence > 1:
            errors.append("confidence_out_of_range")
    return errors


async def publish_evidence(
    db: AsyncSession,
    *,
    source_type: str,
    payload: Mapping[str, Any],
    model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish one validated MATH/ML/OPTUNA artifact, fail-closed."""
    source = str(source_type).upper()
    if source not in {"MATH", "ML", "OPTUNA"}:
        return {"published": False, "reasons": ["unsupported_source_type"]}
    if source == "ML":
        if model is None:
            return {"published": False, "reasons": ["missing_model"]}
        return await publish_ml_evidence(db, model=model, payload=payload)
    errors = validate_evidence(payload)
    if errors:
        return {"published": False, "reasons": errors}
    params = dict(payload)
    for key in (
        "cycle_id", "profile_id", "profile_version_id", "lower", "upper",
        "baseline_metric", "candidate_metric", "delta_metric", "expected_ev",
    ):
        params.setdefault(key, None)
    params["source_type"] = source
    params["limitations"] = json.dumps(payload.get("limitations") or [])
    row = (await db.execute(text("""
        INSERT INTO ml_evidence_registry (
            cycle_id, profile_id, profile_version_id, model_id,
            source_type, source_version, dataset_hash, window_from, window_to,
            target_path, indicator, operator, lower, upper,
            baseline_metric, candidate_metric, delta_metric, expected_ev,
            ci95_lower, ci95_upper, raw_n, effective_n, independent_windows,
            symbols, confidence, status, limitations
        ) VALUES (
            :cycle_id, :profile_id, :profile_version_id, NULL,
            :source_type, :source_version, :dataset_hash, :window_from, :window_to,
            :target_path, :indicator, :operator, :lower, :upper,
            :baseline_metric, :candidate_metric, :delta_metric, :expected_ev,
            :ci95_lower, :ci95_upper, :raw_n, :effective_n, :independent_windows,
            :symbols, :confidence, 'VALID', CAST(:limitations AS JSONB)
        )
        ON CONFLICT (cycle_id, source_type, source_version, target_path)
        DO NOTHING
        RETURNING evidence_id
    """), params)).scalar_one_or_none()
    return {"published": row is not None, "evidence_id": str(row) if row else None}


async def publish_ml_evidence(
    db: AsyncSession,
    *,
    model: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    allowed, reasons = can_publish_ml_evidence(model)
    errors = validate_evidence(payload)
    if not allowed or errors:
        return {"published": False, "reasons": [*reasons, *errors]}

    params = dict(payload)
    params["model_id"] = str(model["id"])
    params["limitations"] = json.dumps(payload.get("limitations") or [])
    row = (await db.execute(text("""
        INSERT INTO ml_evidence_registry (
            cycle_id, profile_id, profile_version_id, model_id,
            source_type, source_version, dataset_hash, window_from, window_to,
            target_path, indicator, operator, lower, upper,
            baseline_metric, candidate_metric, delta_metric, expected_ev,
            ci95_lower, ci95_upper, raw_n, effective_n, independent_windows,
            symbols, confidence, status, limitations
        ) VALUES (
            :cycle_id, :profile_id, :profile_version_id, CAST(:model_id AS UUID),
            'ML', :source_version, :dataset_hash, :window_from, :window_to,
            :target_path, :indicator, :operator, :lower, :upper,
            :baseline_metric, :candidate_metric, :delta_metric, :expected_ev,
            :ci95_lower, :ci95_upper, :raw_n, :effective_n, :independent_windows,
            :symbols, :confidence, 'VALID', CAST(:limitations AS JSONB)
        )
        ON CONFLICT (cycle_id, source_type, source_version, target_path)
        DO NOTHING
        RETURNING evidence_id
    """), params)).scalar_one_or_none()
    return {"published": row is not None, "evidence_id": str(row) if row else None}
