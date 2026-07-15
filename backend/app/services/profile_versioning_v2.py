"""Immutable profile and score-engine version helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def content_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def score_payload_from_profile(config: dict) -> dict:
    """Return the immutable scoring subset used by one profile version."""
    scoring = config.get("scoring") or {}
    return {
        "rules": scoring.get("generated_rules") or scoring.get("rules") or [],
        "weights": scoring.get("weights") or {},
        "thresholds": scoring.get("thresholds") or {},
        "selected_rule_ids": scoring.get("selected_rule_ids") or [],
    }


async def ensure_current_profile_version(
    db: AsyncSession,
    *,
    profile_id: UUID,
    config: dict,
    is_shadow_only: bool,
) -> tuple[UUID, UUID, bool]:
    """Create/reuse the exact current profile snapshot without backfilling history.

    Returns ``(profile_version_id, score_engine_version_id, created)``.
    The per-profile advisory lock keeps concurrent workers idempotent.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:profile_id))"),
        {"profile_id": str(profile_id)},
    )
    profile_hash = content_hash(config)
    score_payload = score_payload_from_profile(config)
    score_hash = content_hash(score_payload)
    score_engine_version_id = (await db.execute(text("""
        INSERT INTO score_engine_versions (
            config_hash, rules, weights, thresholds, selected_rule_ids, status
        ) VALUES (
            :config_hash, CAST(:rules AS JSONB), CAST(:weights AS JSONB),
            CAST(:thresholds AS JSONB), CAST(:selected_rule_ids AS JSONB),
            'BASELINE'
        )
        ON CONFLICT (config_hash) DO UPDATE SET config_hash = EXCLUDED.config_hash
        RETURNING id
    """), {
        "config_hash": score_hash,
        "rules": json.dumps(score_payload["rules"]),
        "weights": json.dumps(score_payload["weights"]),
        "thresholds": json.dumps(score_payload["thresholds"]),
        "selected_rule_ids": json.dumps(score_payload["selected_rule_ids"]),
    })).scalar_one()

    status = "SHADOW" if is_shadow_only else "CHAMPION"
    idempotency_key = f"baseline-v2:{profile_id}:{profile_hash}"
    existing = (await db.execute(text("""
        SELECT id
          FROM profile_versions
         WHERE idempotency_key = :idempotency_key
         LIMIT 1
    """), {"idempotency_key": idempotency_key})).scalar_one_or_none()
    if existing:
        await db.execute(text("""
            UPDATE profile_versions
               SET status = 'ARCHIVED', is_active = false, deactivated_at = now()
             WHERE profile_id = :profile_id
               AND status = :status
               AND id <> :existing_id
        """), {
            "profile_id": str(profile_id),
            "status": status,
            "existing_id": str(existing),
        })
        await db.execute(text("""
            UPDATE profile_versions
               SET config = CAST(:config AS JSONB), config_hash = :config_hash,
                   score_engine_version_id = :score_engine_version_id,
                   status = :status, is_active = :is_active,
                   activated_at = COALESCE(activated_at, now()),
                   deactivated_at = NULL
             WHERE id = :existing_id
        """), {
            "config": json.dumps(config),
            "config_hash": profile_hash,
            "score_engine_version_id": str(score_engine_version_id),
            "status": status,
            "is_active": not is_shadow_only,
            "existing_id": str(existing),
        })
        return existing, score_engine_version_id, False

    parent_id = await db.scalar(text("""
        SELECT id FROM profile_versions
         WHERE profile_id = :profile_id
         ORDER BY version_number DESC
         LIMIT 1
    """), {"profile_id": str(profile_id)})
    version_number = int(await db.scalar(text("""
        SELECT COALESCE(MAX(version_number), 0) + 1
          FROM profile_versions
         WHERE profile_id = :profile_id
    """), {"profile_id": str(profile_id)}) or 1)
    await db.execute(text("""
        UPDATE profile_versions
           SET status = 'ARCHIVED', is_active = false, deactivated_at = now()
         WHERE profile_id = :profile_id AND status = :status
    """), {"profile_id": str(profile_id), "status": status})
    version_id = uuid4()
    await db.execute(text("""
        INSERT INTO profile_versions (
            id, profile_id, version_number, config, mutation_reason, is_active,
            parent_version_id, config_hash, score_engine_version_id, status,
            activated_at, source_recommendation_ids, idempotency_key
        ) VALUES (
            :id, :profile_id, :version_number, CAST(:config AS JSONB),
            'current_profile_baseline_v2', :is_active,
            :parent_version_id, :config_hash, :score_engine_version_id, :status,
            now(), '[]'::jsonb, :idempotency_key
        )
    """), {
        "id": str(version_id),
        "profile_id": str(profile_id),
        "version_number": version_number,
        "config": json.dumps(config),
        "is_active": not is_shadow_only,
        "parent_version_id": str(parent_id) if parent_id else None,
        "config_hash": profile_hash,
        "score_engine_version_id": str(score_engine_version_id),
        "status": status,
        "idempotency_key": idempotency_key,
    })
    return version_id, score_engine_version_id, True


async def create_shadow_profile_version(
    db: AsyncSession,
    *,
    profile_id: UUID,
    config: dict,
    cycle_id: UUID,
    origin_profile_id: UUID | None,
) -> UUID:
    """Create one idempotent SHADOW version without mutating a champion."""
    idempotency_key = f"pi-calibration:{cycle_id}:{profile_id}"
    existing = await db.scalar(text(
        "SELECT id FROM profile_versions WHERE idempotency_key = :key"
    ), {"key": idempotency_key})
    if existing:
        return existing

    parent_id = None
    if origin_profile_id:
        parent_id = await db.scalar(text("""
            SELECT id FROM profile_versions
             WHERE profile_id = :profile_id
             ORDER BY version_number DESC
             LIMIT 1
        """), {"profile_id": str(origin_profile_id)})
    version_number = int(await db.scalar(text("""
        SELECT COALESCE(MAX(version_number), 0) + 1
          FROM profile_versions
         WHERE profile_id = :profile_id
    """), {"profile_id": str(profile_id)}) or 1)
    scoring = config.get("scoring") or {}
    score_payload = {
        "rules": scoring.get("generated_rules") or [],
        "weights": scoring.get("weights") or {},
        "thresholds": scoring.get("thresholds") or {},
        "selected_rule_ids": scoring.get("selected_rule_ids") or [],
    }
    score_hash = content_hash(score_payload)
    score_engine_version_id = (await db.execute(text("""
        INSERT INTO score_engine_versions (
            config_hash, rules, weights, thresholds, selected_rule_ids, status
        ) VALUES (
            :config_hash, CAST(:rules AS JSONB), CAST(:weights AS JSONB),
            CAST(:thresholds AS JSONB), CAST(:selected_rule_ids AS JSONB), 'SHADOW'
        )
        ON CONFLICT (config_hash) DO UPDATE SET config_hash = EXCLUDED.config_hash
        RETURNING id
    """), {
        "config_hash": score_hash,
        "rules": json.dumps(score_payload["rules"]),
        "weights": json.dumps(score_payload["weights"]),
        "thresholds": json.dumps(score_payload["thresholds"]),
        "selected_rule_ids": json.dumps(score_payload["selected_rule_ids"]),
    })).scalar_one()
    version_id = uuid4()
    await db.execute(text("""
        INSERT INTO profile_versions (
            id, profile_id, version_number, config, mutation_reason, is_active,
            parent_version_id, config_hash, score_engine_version_id,
            source_cycle_id, status,
            source_recommendation_ids, idempotency_key
        ) VALUES (
            :id, :profile_id, :version_number, CAST(:config AS JSONB),
            'profile_intelligence_calibration_challenger', false,
            :parent_version_id, :config_hash, :score_engine_version_id,
            :source_cycle_id, 'SHADOW',
            '[]'::jsonb, :idempotency_key
        )
    """), {
        "id": str(version_id),
        "profile_id": str(profile_id),
        "version_number": version_number,
        "config": json.dumps(config),
        "parent_version_id": str(parent_id) if parent_id else None,
        "config_hash": content_hash(config),
        "score_engine_version_id": str(score_engine_version_id),
        "source_cycle_id": str(cycle_id),
        "idempotency_key": idempotency_key,
    })
    return version_id
