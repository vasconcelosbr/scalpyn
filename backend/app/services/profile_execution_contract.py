"""Immutable identity, version and runtime-parity contract for L3 profiles."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile import Profile
from ..models.profile_audit_log import ProfileAuditLog
from .profile_config_validation import validate_profile_config
from .profile_runtime_config import canonical_hash, canonical_profile_config_hash
from .profile_versioning_v2 import (
    create_shadow_profile_version,
    ensure_current_profile_version,
)


EXECUTION_SECTIONS = ("filters", "signals", "entry_triggers", "block_rules")
CONTRACT_VERSION = "l3_profile_execution_contract_v1"


class ProfileContractConflict(ValueError):
    """Raised when an optimistic writer targets stale profile state."""


def section_hashes(config: Mapping[str, Any] | None) -> dict[str, str]:
    source = dict(config or {})
    return {section: canonical_hash(source.get(section) or {}) for section in EXECUTION_SECTIONS}


def _condition_material(section: str, condition: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "section": section,
        "id": condition.get("id"),
        "indicator": condition.get("indicator") or condition.get("field"),
        "left": condition.get("left"),
        "right": condition.get("right"),
        "operator": condition.get("operator"),
        "value": condition.get("value"),
        "min": condition.get("min"),
        "max": condition.get("max"),
        "period": condition.get("period"),
        "timeframe": condition.get("timeframe"),
        "required": bool(condition.get("required", False)),
        "enabled": condition.get("enabled", True) is not False,
    }


def required_condition_contract(config: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return stable fingerprints for explicitly required executable conditions."""
    source = dict(config or {})
    required: list[dict[str, Any]] = []
    for section in ("filters", "signals", "entry_triggers"):
        node = source.get(section) or {}
        for condition in node.get("conditions") or []:
            if not isinstance(condition, Mapping) or condition.get("enabled", True) is False:
                continue
            if not condition.get("required", False):
                continue
            material = _condition_material(section, condition)
            required.append({**material, "fingerprint": canonical_hash(material)})
    return sorted(required, key=lambda item: item["fingerprint"])


def build_execution_contract_snapshot(
    *,
    profile_id: Any,
    profile_name: str | None,
    profile_config: Mapping[str, Any] | None,
    profile_version_id: Any,
    version_profile_id: Any,
    version_config: Mapping[str, Any] | None,
    version_config_hash: str | None,
    active_version_count: int = 0,
) -> dict[str, Any]:
    """Compare authoring projection, immutable version and frozen runtime input."""
    projection = deepcopy(dict(profile_config or {}))
    version = deepcopy(dict(version_config or {}))
    projection_hash = canonical_profile_config_hash(projection)
    computed_version_hash = (
        canonical_profile_config_hash(version) if profile_version_id else None
    )
    projection_sections = section_hashes(projection)
    version_sections = section_hashes(version) if profile_version_id else {
        section: None for section in EXECUTION_SECTIONS
    }

    expected_required = required_condition_contract(version)
    runtime_required = required_condition_contract(projection)
    expected_fingerprints = {item["fingerprint"] for item in expected_required}
    runtime_fingerprints = {item["fingerprint"] for item in runtime_required}
    missing = [
        item for item in expected_required
        if item["fingerprint"] not in runtime_fingerprints
    ]
    unexpected = [
        item for item in runtime_required
        if item["fingerprint"] not in expected_fingerprints
    ]

    reason_codes: list[str] = []
    if profile_version_id is None:
        reason_codes.append("PROFILE_VERSION_MISSING")
    if active_version_count > 1:
        reason_codes.append("MULTIPLE_ACTIVE_PROFILE_VERSIONS")
    if profile_version_id is not None and str(version_profile_id) != str(profile_id):
        reason_codes.append("PROFILE_ID_MISMATCH")
    if profile_version_id is not None and computed_version_hash != version_config_hash:
        reason_codes.append("PROFILE_VERSION_CONFIG_HASH_MISMATCH")
    if profile_version_id is not None and projection_hash != computed_version_hash:
        reason_codes.append("CONFIG_CONTRACT_MISMATCH")
    if missing:
        reason_codes.append("REQUIRED_CONDITION_MISSING")
    if unexpected:
        reason_codes.append("REQUIRED_CONDITION_UNEXPECTED")

    sections = {
        section: {
            "profile_projection_hash": projection_sections[section],
            "version_hash": version_sections[section],
            "runtime_hash": projection_sections[section],
            "match": (
                profile_version_id is not None
                and projection_sections[section] == version_sections[section]
            ),
        }
        for section in EXECUTION_SECTIONS
    }
    contract_valid = not reason_codes
    return {
        "contract_version": CONTRACT_VERSION,
        "profile_id": str(profile_id),
        "profile_name": profile_name,
        "profile_version_id": str(profile_version_id) if profile_version_id else None,
        "version_profile_id": str(version_profile_id) if version_profile_id else None,
        "profile_projection_hash": projection_hash,
        "version_config_hash": version_config_hash,
        "computed_version_hash": computed_version_hash,
        "runtime_hash": projection_hash,
        "sections": sections,
        "required_conditions_hash": canonical_hash(runtime_required),
        "expected_required_conditions_hash": canonical_hash(expected_required),
        "required_conditions": runtime_required,
        "expected_required_conditions": expected_required,
        "missing_required_conditions": missing,
        "unexpected_required_conditions": unexpected,
        "active_version_count": active_version_count,
        "contract_valid": contract_valid,
        "status": "MATCH" if contract_valid else "MISMATCH",
        "reason_codes": reason_codes,
        "profile_projection": projection,
        "version_snapshot": version if profile_version_id else None,
    }


async def load_profile_execution_snapshots(
    db: AsyncSession,
    profile_ids: Iterable[UUID],
    *,
    user_id: UUID | None = None,
) -> dict[UUID, dict[str, Any]]:
    """Load profile plus active immutable version in one statement snapshot."""
    ids = sorted(set(profile_ids), key=str)
    if not ids:
        return {}
    ownership_clause = "AND p.user_id = :user_id" if user_id else ""
    statement = text(f"""
        SELECT p.id AS profile_id,
               p.user_id,
               p.name AS profile_name,
               p.is_shadow_only,
               p.profile_version,
               p.config AS profile_config,
               pv.id AS profile_version_id,
               pv.profile_id AS version_profile_id,
               pv.config AS version_config,
               pv.config_hash AS version_config_hash,
               COALESCE(pv.active_version_count, 0) AS active_version_count
          FROM profiles p
          LEFT JOIN LATERAL (
              SELECT current_version.*,
                     COUNT(*) OVER () AS active_version_count
                FROM profile_versions current_version
               WHERE current_version.profile_id = p.id
                 AND (
                     (
                         p.is_shadow_only IS TRUE
                         AND current_version.status = 'SHADOW'
                     )
                     OR (
                         p.is_shadow_only IS NOT TRUE
                         AND current_version.is_active IS TRUE
                         AND current_version.status = 'CHAMPION'
                     )
                 )
               ORDER BY current_version.version_number DESC,
                        current_version.created_at DESC
               LIMIT 1
          ) pv ON TRUE
         WHERE p.id IN :profile_ids
           {ownership_clause}
         ORDER BY p.id
    """).bindparams(bindparam("profile_ids", expanding=True))
    params: dict[str, Any] = {"profile_ids": ids}
    if user_id:
        params["user_id"] = str(user_id)
    rows = (await db.execute(statement, params)).mappings().all()
    snapshots: dict[UUID, dict[str, Any]] = {}
    for row in rows:
        snapshot = build_execution_contract_snapshot(
            profile_id=row["profile_id"],
            profile_name=row["profile_name"],
            profile_config=row["profile_config"],
            profile_version_id=row["profile_version_id"],
            version_profile_id=row["version_profile_id"],
            version_config=row["version_config"],
            version_config_hash=row["version_config_hash"],
            active_version_count=(
                min(int(row["active_version_count"] or 0), 1)
                if row["is_shadow_only"]
                else int(row["active_version_count"] or 0)
            ),
        )
        snapshots[row["profile_id"]] = {
            "name": row["profile_name"],
            "version": row["profile_version"],
            "version_id": row["profile_version_id"],
            "config": deepcopy(row["profile_config"] or {}),
            "contract": snapshot,
        }
    return snapshots


async def lock_profiles_for_update(
    db: AsyncSession, *, user_id: UUID, profile_ids: Sequence[UUID]
) -> dict[UUID, Profile]:
    """Lock a deterministic profile set so a batch cannot mix versions."""
    ids = sorted(set(profile_ids), key=str)
    if len(ids) != len(profile_ids):
        raise ValueError("duplicate profile_id in update batch")
    rows = (
        await db.execute(
            select(Profile)
            .where(Profile.user_id == user_id, Profile.id.in_(ids))
            .order_by(Profile.id)
            .with_for_update()
        )
    ).scalars().all()
    return {row.id: row for row in rows}


async def activate_profile_config(
    db: AsyncSession,
    *,
    profile: Profile,
    config: Mapping[str, Any],
    changed_by: UUID,
    change_source: str,
    change_description: str,
    expected_profile_version_id: UUID | None = None,
    expected_profile_config_hash: str | None = None,
    require_feature_identity: bool = True,
    previous_config_override: Mapping[str, Any] | None = None,
    shadow_cycle_id: UUID | None = None,
    origin_profile_id: UUID | None = None,
) -> dict[str, Any]:
    """Atomically project validated config and activate its immutable version."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:profile_id))"),
        {"profile_id": str(profile.id)},
    )
    version_status = "SHADOW" if bool(getattr(profile, "is_shadow_only", False)) else "CHAMPION"
    active = (
        await db.execute(
            text("""
                SELECT id, config_hash
                  FROM profile_versions
                 WHERE profile_id = :profile_id
                   AND status = :status
                   AND (:status = 'SHADOW' OR is_active IS TRUE)
                 ORDER BY version_number DESC, created_at DESC
                 LIMIT 1
            """),
            {"profile_id": str(profile.id), "status": version_status},
        )
    ).mappings().one_or_none()
    current_hash = canonical_profile_config_hash(profile.config or {})
    if expected_profile_version_id is not None:
        actual_version_id = active["id"] if active else None
        if str(actual_version_id) != str(expected_profile_version_id):
            raise ProfileContractConflict(
                "PROFILE_VERSION_CONFLICT: expected_profile_version_id is stale"
            )
    if (
        expected_profile_config_hash is not None
        and current_hash != expected_profile_config_hash
    ):
        raise ProfileContractConflict(
            "PROFILE_CONFIG_HASH_CONFLICT: expected_profile_config_hash is stale"
        )

    validated = validate_profile_config(
        dict(config), require_feature_identity=require_feature_identity
    )
    previous_config = deepcopy(
        dict(previous_config_override)
        if previous_config_override is not None
        else dict(profile.config or {})
    )
    previous_timestamp = getattr(profile, "profile_version", None)
    new_timestamp = datetime.now(timezone.utc)
    profile.config = deepcopy(validated)
    profile.profile_version = new_timestamp
    if shadow_cycle_id is not None:
        if not bool(getattr(profile, "is_shadow_only", False)):
            raise ValueError("shadow_cycle_id requires an is_shadow_only profile")
        shadow_idempotency_key = (
            f"pi-calibration:{shadow_cycle_id}:{profile.id}"
        )
        existing_shadow_id = await db.scalar(
            text("SELECT id FROM profile_versions WHERE idempotency_key = :key"),
            {"key": shadow_idempotency_key},
        )
        version_id = await create_shadow_profile_version(
            db,
            profile_id=profile.id,
            config=validated,
            cycle_id=shadow_cycle_id,
            origin_profile_id=origin_profile_id,
        )
        score_engine_version_id = await db.scalar(
            text(
                "SELECT score_engine_version_id FROM profile_versions WHERE id = :id"
            ),
            {"id": str(version_id)},
        )
        created = existing_shadow_id is None
    else:
        version_id, score_engine_version_id, created = await ensure_current_profile_version(
            db,
            profile_id=profile.id,
            config=validated,
            is_shadow_only=bool(getattr(profile, "is_shadow_only", False)),
        )
    new_hash = canonical_profile_config_hash(validated)
    new_section_hashes = section_hashes(validated)
    previous_hash = canonical_profile_config_hash(previous_config)
    previous_section_hashes = section_hashes(previous_config)
    audit_contract = {
        "previous_profile_version_id": str(active["id"]) if active else None,
        "new_profile_version_id": str(version_id),
        "previous_profile_config_hash": previous_hash,
        "new_profile_config_hash": new_hash,
        "previous_section_hashes": previous_section_hashes,
        "new_section_hashes": new_section_hashes,
    }
    db.add(
        ProfileAuditLog(
            user_id=profile.user_id,
            profile_id=profile.id,
            changed_by=changed_by,
            change_source=change_source,
            change_description=(
                f"{change_description} | execution_contract="
                f"{json.dumps(audit_contract, sort_keys=True)}"
            ),
            previous_config=previous_config,
            new_config=deepcopy(validated),
            previous_profile_version=previous_timestamp,
            new_profile_version=new_timestamp,
        )
    )
    return {
        "profile_id": str(profile.id),
        "profile_version_id": str(version_id),
        "score_engine_version_id": str(score_engine_version_id),
        "previous_profile_version_id": str(active["id"]) if active else None,
        "previous_profile_config_hash": previous_hash,
        "previous_section_hashes": previous_section_hashes,
        "profile_config_hash": new_hash,
        "section_hashes": new_section_hashes,
        "version_created": created,
    }
