"""Read-only governance for native point-in-time ML captures."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text

from .feature_contract_v2 import snapshot_hash

CAPTURE_CONTRACT = "point-in-time-v1"
EXTRACTOR_VERSION = "feature-engine-v2"
SCHEMA_VERSION = "entry_features_v2"
CANARY_LIMIT = 50

ALERT_RULES = {
    "capture_rate_zero": ("total_native", "eq", 0),
    "official_invalid_gt_0": ("official_invalid", "gt", 0),
    "hash_mismatch_gt_0": ("hash_mismatch", "gt", 0),
    "lineage_incomplete_gt_0": ("lineage_incomplete", "gt", 0),
    "future_timestamp_gt_0": ("future_timestamp", "gt", 0),
    "legacy_row_in_official_dataset_gt_0": (
        "legacy_rows_in_official_dataset",
        "gt",
        0,
    ),
}

LINEAGE_REQUIREMENTS = {
    "L1_SPECTRUM": {"profile_id": "OPTIONAL", "decision_id": "NOT_APPLICABLE"},
    "L3": {"profile_id": "OPTIONAL", "decision_id": "REQUIRED"},
    "L3_REJECTED": {"profile_id": "OPTIONAL", "decision_id": "OPTIONAL"},
    "L3_LAB": {"profile_id": "REQUIRED", "decision_id": "NOT_APPLICABLE"},
    "L3_SIMULATED": {"profile_id": "OPTIONAL", "decision_id": "NOT_APPLICABLE"},
}

LANE_CONTRACT = {
    "LIGHTGBM": {"lane": "L1", "sources": {"L1_SPECTRUM"}, "profile_required": False},
    "CATBOOST": {
        "lane": "L3",
        "sources": {"L3", "L3_REJECTED", "L3_LAB", "L3_SIMULATED"},
        "profile_required": False,
    },
}


def lineage_errors(row: Mapping[str, Any]) -> list[str]:
    requirements = LINEAGE_REQUIREMENTS.get(row.get("source"))
    if requirements is None:
        return ["invalid_source"]
    errors = [
        f"missing_{field}"
        for field, rule in requirements.items()
        if rule == "REQUIRED" and not row.get(field)
    ]
    if row.get("lineage_status") != "EXACT":
        errors.append("invalid_lineage_status")
    return errors


def official_row_errors(
    row: Mapping[str, Any],
    start_at: datetime | None,
    *,
    reference_now: datetime | None = None,
) -> list[str]:
    if start_at is None:
        return ["data_collection_not_started"]

    errors: list[str] = []
    required = (
        "features_snapshot",
        "features_captured_at",
        "feature_hash",
        "feature_extractor_version",
        "feature_schema_version",
        "profile_version_id",
        "score_engine_version_id",
    )
    errors.extend(f"missing_{name}" for name in required if row.get(name) is None)

    if row.get("capture_contract_version") != CAPTURE_CONTRACT:
        errors.append("invalid_contract")
    if row.get("feature_extractor_version") not in (None, EXTRACTOR_VERSION):
        errors.append("invalid_extractor")
    if row.get("feature_schema_version") not in (None, SCHEMA_VERSION):
        errors.append("invalid_schema")

    captured = row.get("features_captured_at")
    if captured and captured < start_at:
        errors.append("pre_native_row")
    observed_at = reference_now or datetime.now(timezone.utc)
    if captured and captured > observed_at:
        errors.append("future_timestamp")
    if not row.get("eligible_for_training"):
        errors.append("not_training_eligible")

    errors.extend(lineage_errors(row))
    snapshot = row.get("features_snapshot")
    if isinstance(snapshot, Mapping) and snapshot.get("atr_pct") is None:
        errors.append("missing_atr_pct")
    if snapshot is not None and row.get("feature_hash"):
        try:
            if snapshot_hash(snapshot) != row["feature_hash"]:
                errors.append("hash_mismatch")
        except (TypeError, ValueError):
            errors.append("invalid_snapshot_json")
    return errors


def classify_native_row(
    row: Mapping[str, Any],
    start_at: datetime | None,
    *,
    reference_now: datetime | None = None,
) -> tuple[str, list[str]]:
    errors = official_row_errors(row, start_at, reference_now=reference_now)
    if not row.get("eligible_for_training"):
        return "historical_quarantine", errors
    if errors:
        return "official_invalid", errors
    return "official_native_eligible", []


def approval_guard_reason(metrics: Mapping[str, int], minimum_required: int) -> str | None:
    gates = (
        ("official_invalid", "OFFICIAL_DATASET_INVALID"),
        ("legacy_rows_in_official_dataset", "LEGACY_ROWS_PRESENT"),
        ("lineage_incomplete", "INCOMPLETE_LINEAGE"),
        ("hash_mismatch", "SNAPSHOT_HASH_MISMATCH"),
    )
    for key, reason in gates:
        if int(metrics.get(key, 0)) > 0:
            return reason
    if int(metrics.get("official_native_eligible", 0)) < minimum_required:
        return "INSUFFICIENT_PROVEN_TEMPORAL_DATA"
    return None


async def audit_native_capture(
    db,
    start_at: datetime | None,
    limit: int = CANARY_LIMIT,
    *,
    full_window: bool = False,
    audit_query_cutoff: datetime | None = None,
) -> dict[str, Any]:
    await db.execute(text("SET TRANSACTION READ ONLY"))
    if start_at is None:
        return {
            "status": "DATA_COLLECTION_NOT_STARTED",
            "native_capture_start_at": None,
            "total_native": 0,
            "official_native_eligible": 0,
            "official_invalid": 0,
            "historical_quarantine": 0,
            "legacy_rows_in_official_dataset": 0,
            "by_source": {},
            "last_capture_at": None,
        }

    if audit_query_cutoff is None:
        audit_query_cutoff = (
            await db.execute(text("SELECT clock_timestamp() AS observed_at"))
        ).mappings().one()["observed_at"]
    if audit_query_cutoff.tzinfo is None:
        raise ValueError("invalid_audit_query_cutoff_timezone")

    limit_clause = "" if full_window else "LIMIT :limit"
    params: dict[str, Any] = {
        "start": start_at,
        "audit_query_cutoff": audit_query_cutoff,
    }
    if not full_window:
        params["limit"] = min(max(limit, 1), CANARY_LIMIT)
    rows = (
        await db.execute(
            text(
                f"""
                SELECT id, source, profile_id, ranking_id, decision_id,
                       profile_version_id, score_engine_version_id,
                       features_snapshot, features_captured_at, feature_hash,
                       feature_extractor_version, feature_schema_version,
                       capture_contract_version, lineage_status,
                       eligible_for_training, created_at, completed_at
                FROM shadow_trades
                WHERE created_at >= :start
                  AND created_at <= :audit_query_cutoff
                  AND capture_contract_version = :capture_contract
                ORDER BY created_at DESC, id DESC
                {limit_clause}
                """
            ),
            {**params, "capture_contract": CAPTURE_CONTRACT},
        )
    ).mappings().all()

    checks = [
        classify_native_row(row, start_at, reference_now=audit_query_cutoff)
        for row in rows
    ]
    buckets = [bucket for bucket, _ in checks]
    errors = [row_errors for _, row_errors in checks]

    official_errors = [
        row_errors
        for bucket, row_errors in checks
        if bucket == "official_invalid"
    ]
    historical_errors = [
        row_errors
        for bucket, row_errors in checks
        if bucket == "historical_quarantine"
    ]

    def count_error(reason: str, error_sets=official_errors) -> int:
        return sum(reason in row_errors for row_errors in error_sets)

    official_eligible = buckets.count("official_native_eligible")
    official_invalid = buckets.count("official_invalid")
    quarantine = buckets.count("historical_quarantine")
    by_source: dict[str, dict[str, int]] = {}
    for row, bucket in zip(rows, buckets):
        source = row["source"]
        values = by_source.setdefault(
            source,
            {
                "total_native": 0,
                "official_native_eligible": 0,
                "official_invalid": 0,
                "historical_quarantine": 0,
            },
        )
        values["total_native"] += 1
        values[bucket] += 1

    official_population = official_eligible + official_invalid
    if official_invalid:
        status = "INVALID"
    elif official_population == 0:
        status = "NATIVE_CAPTURE_COLLECTION_IN_PROGRESS"
    elif not full_window and len(rows) < CANARY_LIMIT:
        status = "NATIVE_CAPTURE_COLLECTION_IN_PROGRESS"
    else:
        status = "VALID"

    captures = [row["features_captured_at"] for row in rows if row["features_captured_at"]]
    missing_lineage = sum(
        any(
            item in row_errors
            for item in ("missing_profile_id", "missing_decision_id", "invalid_lineage_status")
        )
        for row_errors in official_errors
    )
    historical_missing_lineage = sum(
        any(
            item in row_errors
            for item in ("missing_profile_id", "missing_decision_id", "invalid_lineage_status")
        )
        for row_errors in historical_errors
    )
    legacy_official = sum(
        bucket == "official_invalid"
        and any(item in row_errors for item in ("invalid_contract", "pre_native_row"))
        for bucket, row_errors in checks
    )
    hash_mismatch = count_error("hash_mismatch")
    return {
        "status": status,
        "historical_status": "INVALIDO_HISTORICO" if quarantine else "NONE",
        "scope": "full_window" if full_window else "recent_canary",
        "native_capture_start_at": start_at.isoformat(),
        "audit_query_cutoff": audit_query_cutoff.isoformat(),
        "total_native": len(rows),
        "official_native_eligible": official_eligible,
        "official_invalid": official_invalid,
        "historical_quarantine": quarantine,
        "total_valid": official_eligible,
        "total_invalid": official_invalid,
        "validity_rate": (
            official_eligible / official_population if official_population else None
        ),
        "missing_snapshot": count_error("missing_features_snapshot"),
        "missing_timestamp": count_error("missing_features_captured_at"),
        "missing_hash": count_error("missing_feature_hash"),
        "missing_versions": count_error("missing_feature_extractor_version")
        + count_error("missing_feature_schema_version"),
        "missing_lineage": missing_lineage,
        "lineage_incomplete": missing_lineage,
        "missing_atr_pct": count_error("missing_atr_pct"),
        "historical_missing_lineage": historical_missing_lineage,
        "historical_missing_atr_pct": count_error(
            "missing_atr_pct", historical_errors
        ),
        "hash_match": len(rows) - hash_mismatch,
        "hash_mismatch": hash_mismatch,
        "historical_hash_mismatch": count_error(
            "hash_mismatch", historical_errors
        ),
        "future_timestamp": count_error("future_timestamp"),
        "legacy_rows": quarantine,
        "legacy_rows_in_official_dataset": legacy_official,
        "duplicate_identity": 0,
        "semantic_conflict": 0,
        "idempotent_retry": 0,
        "invalid_source": count_error("invalid_source"),
        "invalid_profile": count_error("missing_profile_id"),
        "labels_completed": sum(row["completed_at"] is not None for row in rows),
        "labels_pending": sum(row["completed_at"] is None for row in rows),
        "by_source": by_source,
        "last_capture_at": max(captures).isoformat() if captures else None,
    }
