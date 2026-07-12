"""Read-only governance for the point-in-time-v1 official ML dataset."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text

from .feature_contract_v2 import snapshot_hash

CAPTURE_CONTRACT = "point-in-time-v1"
EXTRACTOR_VERSION = "feature-engine-v2"
SCHEMA_VERSION = "entry_features_v2"
CANARY_LIMIT = 50

LINEAGE_REQUIREMENTS = {
    "L1_SPECTRUM": {"profile_id": "OPTIONAL", "ranking_id": "OPTIONAL", "decision_id": "NOT_APPLICABLE"},
    "L3": {"profile_id": "OPTIONAL", "ranking_id": "OPTIONAL", "decision_id": "REQUIRED"},
    "L3_REJECTED": {"profile_id": "OPTIONAL", "ranking_id": "OPTIONAL", "decision_id": "OPTIONAL"},
    "L3_LAB": {"profile_id": "REQUIRED", "ranking_id": "OPTIONAL", "decision_id": "NOT_APPLICABLE"},
    "L3_SIMULATED": {"profile_id": "OPTIONAL", "ranking_id": "OPTIONAL", "decision_id": "NOT_APPLICABLE"},
}

LANE_CONTRACT = {
    "XGBOOST": {"lane": "L1", "sources": {"L1_SPECTRUM"}, "profile_required": False},
    "LIGHTGBM": {"lane": "L3", "sources": {"L3", "L3_REJECTED", "L3_LAB", "L3_SIMULATED"}, "profile_required": False},
    "CATBOOST": {"lane": "L3", "sources": {"L3", "L3_REJECTED", "L3_LAB", "L3_SIMULATED"}, "profile_required": False},
}

def lineage_errors(row: Mapping[str, Any]) -> list[str]:
    source = row.get("source")
    requirements = LINEAGE_REQUIREMENTS.get(source)
    if requirements is None:
        return ["invalid_source"]
    return [f"missing_{field}" for field, rule in requirements.items() if rule == "REQUIRED" and not row.get(field)]

def official_row_errors(row: Mapping[str, Any], start_at: datetime | None) -> list[str]:
    if start_at is None:
        return ["data_collection_not_started"]
    errors: list[str] = []
    required = ("features_snapshot", "features_captured_at", "feature_hash", "feature_extractor_version", "feature_schema_version")
    errors.extend(f"missing_{name}" for name in required if row.get(name) is None)
    if row.get("capture_contract_version") != CAPTURE_CONTRACT: errors.append("invalid_contract")
    if row.get("feature_extractor_version") not in (None, EXTRACTOR_VERSION): errors.append("invalid_extractor")
    if row.get("feature_schema_version") not in (None, SCHEMA_VERSION): errors.append("invalid_schema")
    captured = row.get("features_captured_at")
    if captured and captured < start_at: errors.append("pre_native_row")
    if captured and captured > datetime.now(timezone.utc): errors.append("future_timestamp")
    if not row.get("eligible_for_training"): errors.append("not_training_eligible")
    errors.extend(lineage_errors(row))
    snapshot = row.get("features_snapshot")
    if snapshot is not None and row.get("feature_hash"):
        try:
            if snapshot_hash(snapshot) != row["feature_hash"]: errors.append("hash_mismatch")
        except (TypeError, ValueError): errors.append("invalid_snapshot_json")
    return errors

def approval_guard_reason(metrics: Mapping[str, int], minimum_required: int) -> str | None:
    gates = (("unproven_temporality_rows", "UNPROVEN_TEMPORALITY_PRESENT"), ("invalid_temporality_rows", "INVALID_TEMPORALITY_PRESENT"), ("legacy_rows", "LEGACY_ROWS_PRESENT"), ("lineage_incomplete", "INCOMPLETE_LINEAGE"), ("hash_mismatch", "SNAPSHOT_HASH_MISMATCH"))
    for key, reason in gates:
        if int(metrics.get(key, 0)) > 0: return reason
    if int(metrics.get("proven_rows", 0)) < minimum_required: return "INSUFFICIENT_PROVEN_TEMPORAL_DATA"
    return None

async def audit_native_capture(db, start_at: datetime | None, limit: int = CANARY_LIMIT) -> dict[str, Any]:
    await db.execute(text("SET TRANSACTION READ ONLY"))
    if start_at is None:
        return {key: 0 for key in ("total_native","total_valid","total_invalid","missing_snapshot","missing_timestamp","missing_hash","missing_versions","missing_lineage","hash_match","hash_mismatch","future_timestamp","legacy_rows","duplicate_identity","semantic_conflict","idempotent_retry","invalid_source","invalid_profile","labels_completed","labels_pending")} | {"status": "DATA_COLLECTION_NOT_STARTED", "native_capture_start_at": None, "legacy_rows_in_official_dataset": 0, "last_capture_at": None, "by_source": {}}
    rows = (await db.execute(text("""SELECT id,source,profile_id,ranking_id,decision_id,features_snapshot,features_captured_at,feature_hash,feature_extractor_version,feature_schema_version,capture_contract_version,eligible_for_training,created_at,completed_at FROM shadow_trades WHERE features_captured_at >= :start ORDER BY features_captured_at,id LIMIT :limit"""), {"start": start_at, "limit": min(max(limit, 1), CANARY_LIMIT)})).mappings().all()
    checks = [official_row_errors(row, start_at) for row in rows]
    invalid = sum(bool(e) for e in checks)
    count=lambda reason: sum(reason in errors for errors in checks)
    by_source: dict[str,int] = {}
    for row in rows: by_source[row["source"]] = by_source.get(row["source"],0)+1
    hashes=count("hash_mismatch")
    missing_lineage=sum(any(x in e for x in ("missing_profile_id","missing_decision_id")) for e in checks)
    return {"status": "NATIVE_CAPTURE_COLLECTION_IN_PROGRESS" if len(rows) < CANARY_LIMIT else ("VALID" if invalid == 0 else "INVALID"), "native_capture_start_at": start_at.isoformat(), "total_native": len(rows), "total_valid": len(rows)-invalid, "total_invalid": invalid, "valid_native": len(rows)-invalid, "invalid_native": invalid, "missing_snapshot":count("missing_features_snapshot"), "missing_timestamp":count("missing_features_captured_at"), "missing_hash":count("missing_feature_hash"), "missing_versions":count("missing_feature_extractor_version")+count("missing_feature_schema_version"), "missing_lineage":missing_lineage, "hash_match":len(rows)-hashes, "hash_mismatch":hashes, "future_timestamp":count("future_timestamp"), "legacy_rows":0, "legacy_rows_in_official_dataset":0, "duplicate_identity":0, "semantic_conflict":0, "idempotent_retry":0, "invalid_source":count("invalid_source"), "invalid_profile":count("missing_profile_id"), "lineage_incomplete":missing_lineage, "labels_completed":sum(row["completed_at"] is not None for row in rows), "labels_pending":sum(row["completed_at"] is None for row in rows), "by_source":by_source, "last_capture_at": rows[-1]["features_captured_at"].isoformat() if rows else None}
