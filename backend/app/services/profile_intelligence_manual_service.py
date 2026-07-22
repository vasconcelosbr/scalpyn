"""Fail-closed, operator-only adjustments for existing L3 profiles.

This module deliberately has no capture, dataset, training, model approval, or
Auto-Pilot dependency.  Statistical gates are retained as warnings; ownership,
version, path, payload, hash, idempotency, and concurrency checks remain hard
gates.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile_intelligence_manual import (
    ProfileIntelligenceManualAdjustment,
    ProfileIntelligenceManualAdjustmentEvent,
)
from .calibration_orchestrator_v2 import content_hash, resolve_stable_path
from .profile_versioning_v2 import score_payload_from_profile


MANUAL_ACTIONS = {
    "ADD_SIGNAL_CONDITION",
    "UPDATE_SIGNAL_THRESHOLD",
    "UPDATE_SIGNAL_RANGE",
    "REMOVE_SIGNAL_CONDITION",
    "ADD_SCORE_BONUS",
    "ADD_SCORE_PENALTY",
    "UPDATE_SCORE_WEIGHT",
    "UPDATE_SCORE_THRESHOLD",
    "ADD_BLOCK_RULE",
    "UPDATE_BLOCK_RULE",
    "REMOVE_BLOCK_RULE",
    "OBSERVE_ONLY",
}

MUTATING_STATES = {"MANUAL_DRAFT", "PENDING_MANUAL_APPROVAL"}
FORBIDDEN_PATH_PARTS = {
    "features_snapshot", "eligible_for_training", "ml_model_id", "model_version",
    "training", "dataset", "label", "shadow_trades", "historical",
}
ACTION_ROOTS = {
    "ADD_SIGNAL_CONDITION": "signals",
    "UPDATE_SIGNAL_THRESHOLD": "signals",
    "UPDATE_SIGNAL_RANGE": "signals",
    "REMOVE_SIGNAL_CONDITION": "signals",
    "ADD_SCORE_BONUS": "scoring",
    "ADD_SCORE_PENALTY": "scoring",
    "UPDATE_SCORE_WEIGHT": "scoring",
    "UPDATE_SCORE_THRESHOLD": "scoring",
    "ADD_BLOCK_RULE": "block_rules",
    "UPDATE_BLOCK_RULE": "block_rules",
    "REMOVE_BLOCK_RULE": "block_rules",
}
ADD_ACTIONS = {"ADD_SIGNAL_CONDITION", "ADD_SCORE_BONUS", "ADD_SCORE_PENALTY", "ADD_BLOCK_RULE"}
REMOVE_ACTIONS = {"REMOVE_SIGNAL_CONDITION", "REMOVE_BLOCK_RULE"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_id(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return value.get("condition_id") or value.get("rule_id") or value.get("id")


def validate_manual_target(action_type: str, target_path: str | None, proposed_value: Any) -> None:
    if action_type not in MANUAL_ACTIONS:
        raise ValueError("unsupported_manual_action")
    if action_type == "OBSERVE_ONLY":
        if target_path or proposed_value is not None:
            raise ValueError("observe_only_cannot_mutate")
        return
    if not target_path or not target_path.startswith("/"):
        raise ValueError("target_path_must_be_absolute")
    lowered = {part.lower() for part in target_path.split("/") if part}
    if lowered.intersection(FORBIDDEN_PATH_PARTS):
        raise ValueError("ml_or_historical_target_forbidden")
    expected_root = ACTION_ROOTS[action_type]
    if target_path.split("/", 2)[1] != expected_root:
        raise ValueError("action_target_scope_mismatch")
    if any(part.isdigit() for part in target_path.split("/") if part):
        raise ValueError("list_index_paths_forbidden")
    if action_type in ADD_ACTIONS and not _stable_id(proposed_value):
        raise ValueError("added_item_requires_stable_id")
    if action_type in REMOVE_ACTIONS and "/by_id/" not in target_path:
        raise ValueError("remove_requires_stable_id_path")


def apply_manual_action(
    config: Mapping[str, Any], action_type: str, target_path: str | None,
    current_value: Any, proposed_value: Any,
) -> dict[str, Any]:
    """Return a bounded copy; never mutate the supplied configuration."""
    validate_manual_target(action_type, target_path, proposed_value)
    result = deepcopy(dict(config))
    if action_type == "OBSERVE_ONLY":
        return result
    assert target_path is not None
    if action_type in ADD_ACTIONS:
        target = resolve_stable_path(result, target_path)
        if not isinstance(target, list) or not isinstance(proposed_value, Mapping):
            raise ValueError("add_target_must_be_list_and_payload_object")
        proposed_item = deepcopy(dict(proposed_value))
        if action_type in {"ADD_SCORE_BONUS", "ADD_SCORE_PENALTY"}:
            points = proposed_item.pop("score", proposed_item.get("points"))
            if not isinstance(points, (int, float)) or isinstance(points, bool):
                raise ValueError("manual_score_rule_requires_numeric_points")
            if action_type == "ADD_SCORE_BONUS" and points <= 0:
                raise ValueError("manual_score_bonus_must_be_positive")
            if action_type == "ADD_SCORE_PENALTY" and points >= 0:
                raise ValueError("manual_score_penalty_must_be_negative")
            proposed_item["points"] = float(points)
            proposed_item["manual_profile_intelligence"] = True
            proposed_item.setdefault("id", _stable_id(proposed_item))
        stable_id = _stable_id(proposed_item)
        if any(_stable_id(item) == stable_id for item in target):
            raise ValueError("stable_id_already_exists")
        target.append(proposed_item)
        return result
    if action_type in REMOVE_ACTIONS:
        parts = target_path.strip("/").split("/")
        if len(parts) < 3 or parts[-2] != "by_id":
            raise ValueError("remove_requires_stable_id_path")
        container = resolve_stable_path(result, "/" + "/".join(parts[:-2]))
        if not isinstance(container, list):
            raise ValueError("remove_target_not_list")
        stable_id = parts[-1]
        item = next((value for value in container if _stable_id(value) == stable_id), None)
        if item is None:
            raise ValueError("stable_id_not_found")
        if item != current_value:
            raise ValueError("current_value_mismatch")
        container.remove(item)
        return result

    actual = resolve_stable_path(result, target_path)
    if actual != current_value:
        raise ValueError("current_value_mismatch")
    parts = target_path.strip("/").split("/")
    parent_path = "/" + "/".join(parts[:-1])
    parent = resolve_stable_path(result, parent_path)
    if not isinstance(parent, dict) or parts[-1] not in parent:
        raise ValueError("target_path_not_found")
    parent[parts[-1]] = deepcopy(proposed_value)
    if action_type == "UPDATE_SCORE_WEIGHT":
        result.setdefault("scoring", {})["manual_weighting_enabled"] = True
    return result


def _public(row: ProfileIntelligenceManualAdjustment) -> dict[str, Any]:
    return {
        "id": str(row.id), "user_id": str(row.user_id),
        "run_id": str(row.run_id) if row.run_id else None,
        "indicator_stat_id": str(row.indicator_stat_id) if row.indicator_stat_id else None,
        "profile_id": str(row.profile_id),
        "base_profile_version_id": str(row.base_profile_version_id),
        "applied_profile_version_id": str(row.applied_profile_version_id) if row.applied_profile_version_id else None,
        "rollback_profile_version_id": str(row.rollback_profile_version_id) if row.rollback_profile_version_id else None,
        "runtime_target_profile_version_id": str(row.runtime_target_profile_version_id) if row.runtime_target_profile_version_id else None,
        "runtime_target_score_engine_version_id": str(row.runtime_target_score_engine_version_id) if row.runtime_target_score_engine_version_id else None,
        "runtime_target_config_hash": row.runtime_target_config_hash,
        "runtime_status": row.runtime_status,
        "runtime_confirmation_source": row.runtime_confirmation_source,
        "runtime_error": row.runtime_error,
        "action_type": row.action_type, "target_path": row.target_path,
        "current_value": row.current_value, "proposed_value": row.proposed_value,
        "before_config": row.before_config, "after_config": row.after_config, "diff": row.diff,
        "evidence_json": row.evidence_json or {}, "statistical_warnings": row.statistical_warnings or [],
        "config_hash_before": row.config_hash_before, "config_hash_after": row.config_hash_after,
        "preview_hash": row.preview_hash, "state": row.state,
        "idempotency_key": row.idempotency_key, "justification": row.justification,
        "risk_confirmed": row.risk_confirmed, "approved_by": str(row.approved_by) if row.approved_by else None,
        "rollback_reason": row.rollback_reason, "mutation_source": row.mutation_source,
        "autopilot_applied": row.autopilot_applied,
        "ml_training_mutated": row.ml_training_mutated,
        "historical_dataset_mutated": row.historical_dataset_mutated,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "previewed_at": row.previewed_at.isoformat() if row.previewed_at else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "rolled_back_at": row.rolled_back_at.isoformat() if row.rolled_back_at else None,
        "runtime_confirmed_at": row.runtime_confirmed_at.isoformat() if row.runtime_confirmed_at else None,
    }


async def _event(db: AsyncSession, row: ProfileIntelligenceManualAdjustment, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
    db.add(ProfileIntelligenceManualAdjustmentEvent(
        adjustment_id=row.id, user_id=row.user_id, event_type=event_type,
        actor_user_id=row.user_id, payload_json=dict(payload or {}),
    ))


async def _eligible_version(db: AsyncSession, user_id: UUID, profile_id: UUID, *, for_update: bool = False) -> Mapping[str, Any]:
    lock = " FOR UPDATE OF p, pv" if for_update else ""
    row = (await db.execute(text(f"""
        SELECT p.id AS profile_id, p.name AS profile_name, p.config AS profile_config,
               p.profile_version, pv.id AS version_id, pv.version_number,
               pv.config AS version_config, pv.config_hash
          FROM profiles p
          JOIN profile_versions pv ON pv.profile_id = p.id
         WHERE p.id = :profile_id AND p.user_id = :user_id
           AND p.is_active IS TRUE AND p.is_shadow_only IS FALSE
           AND p.generated_by IS NULL AND p.generated_from_suggestion_id IS NULL
           AND pv.status = 'CHAMPION' AND pv.is_active IS TRUE
           AND EXISTS (
               SELECT 1 FROM pipeline_watchlists pw
                WHERE pw.profile_id = p.id AND upper(pw.level) = 'L3'
           ){lock}
    """), {"profile_id": str(profile_id), "user_id": str(user_id)})).mappings().first()
    if not row:
        raise ValueError("profile_not_existing_active_l3")
    if content_hash(row["version_config"] or {}) != row["config_hash"]:
        raise ValueError("base_version_hash_invalid")
    if content_hash(row["profile_config"] or {}) != row["config_hash"]:
        raise ValueError("profile_and_champion_config_mismatch")
    return row


class ProfileIntelligenceManualService:
    async def eligible_profiles(self, db: AsyncSession, user_id: UUID) -> list[dict[str, Any]]:
        rows = (await db.execute(text("""
            SELECT DISTINCT p.id, p.name, pv.id AS profile_version_id,
                   pv.version_number, pv.config_hash
              FROM profiles p
              JOIN pipeline_watchlists pw ON pw.profile_id = p.id AND upper(pw.level) = 'L3'
              JOIN profile_versions pv ON pv.profile_id = p.id
                   AND pv.status = 'CHAMPION' AND pv.is_active IS TRUE
             WHERE p.user_id = :user_id AND p.is_active IS TRUE
               AND p.is_shadow_only IS FALSE AND p.generated_by IS NULL
               AND p.generated_from_suggestion_id IS NULL
             ORDER BY p.name
        """), {"user_id": str(user_id)})).mappings().all()
        return [{**dict(row), "id": str(row["id"]), "profile_version_id": str(row["profile_version_id"])} for row in rows]

    async def create(self, db: AsyncSession, user_id: UUID, payload: Mapping[str, Any]) -> dict[str, Any]:
        action = str(payload["action_type"])
        validate_manual_target(action, payload.get("target_path"), payload.get("proposed_value"))
        existing = await db.scalar(select(ProfileIntelligenceManualAdjustment).where(
            ProfileIntelligenceManualAdjustment.user_id == user_id,
            ProfileIntelligenceManualAdjustment.idempotency_key == payload["idempotency_key"],
        ))
        if existing:
            return _public(existing)
        version = await _eligible_version(db, user_id, payload["profile_id"])
        row = ProfileIntelligenceManualAdjustment(
            user_id=user_id, run_id=payload.get("run_id"), indicator_stat_id=payload.get("indicator_stat_id"),
            profile_id=payload["profile_id"], base_profile_version_id=version["version_id"],
            action_type=action, target_path=payload.get("target_path"),
            current_value=payload.get("current_value"), proposed_value=payload.get("proposed_value"),
            evidence_json=dict(payload.get("evidence_json") or {}),
            statistical_warnings=list(payload.get("statistical_warnings") or []),
            idempotency_key=str(payload["idempotency_key"]), state="MANUAL_DRAFT",
        )
        db.add(row); await db.flush()
        await _event(db, row, "MANUAL_ADJUSTMENT_CREATED", {"base_profile_version_id": str(version["version_id"])})
        evidence = dict(payload.get("evidence_json") or {})
        if evidence.get("dataset") == "pi-native-point-in-time-v1":
            await _event(db, row, "MANUAL_ADJUSTMENT_DRAFTED_FROM_SCORE_INTELLIGENCE", {
                "profile_id": str(row.profile_id),
                "profile_version_id": str(evidence.get("profile_version_id") or version["version_id"]),
                "score_engine_version_id": str(evidence.get("score_engine_version_id") or ""),
                "source": evidence.get("source"),
                "score": evidence.get("recommendation", {}).get("score"),
                "current_threshold": evidence.get("recommendation", {}).get("current_threshold"),
                "simulated_threshold": evidence.get("recommendation", {}).get("proposed_threshold"),
            })
        await db.flush()
        return _public(row)

    async def get(self, db: AsyncSession, user_id: UUID, adjustment_id: UUID) -> ProfileIntelligenceManualAdjustment:
        row = await db.scalar(select(ProfileIntelligenceManualAdjustment).where(
            ProfileIntelligenceManualAdjustment.id == adjustment_id,
            ProfileIntelligenceManualAdjustment.user_id == user_id,
        ))
        if not row:
            raise ValueError("manual_adjustment_not_found")
        return row

    async def list(self, db: AsyncSession, user_id: UUID, state: str | None, limit: int) -> list[dict[str, Any]]:
        stmt = select(ProfileIntelligenceManualAdjustment).where(ProfileIntelligenceManualAdjustment.user_id == user_id)
        if state:
            stmt = stmt.where(ProfileIntelligenceManualAdjustment.state == state)
        rows = (await db.execute(stmt.order_by(ProfileIntelligenceManualAdjustment.created_at.desc()).limit(limit))).scalars().all()
        return [_public(row) for row in rows]

    async def update(self, db: AsyncSession, user_id: UUID, adjustment_id: UUID, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = await self.get(db, user_id, adjustment_id)
        if row.state != "MANUAL_DRAFT":
            raise ValueError("only_manual_draft_is_editable")
        action = payload.get("action_type") or row.action_type
        path = payload.get("target_path", row.target_path)
        proposed = payload.get("proposed_value", row.proposed_value)
        validate_manual_target(action, path, proposed)
        for name in ("action_type", "target_path", "current_value", "proposed_value", "evidence_json", "statistical_warnings"):
            if name in payload:
                setattr(row, name, payload[name])
        row.updated_at = _now()
        await _event(db, row, "UPDATED")
        await db.flush(); return _public(row)

    async def preview(self, db: AsyncSession, user_id: UUID, adjustment_id: UUID) -> dict[str, Any]:
        row = await self.get(db, user_id, adjustment_id)
        if row.state not in MUTATING_STATES:
            raise ValueError("manual_adjustment_not_previewable")
        version = await _eligible_version(db, user_id, row.profile_id)
        if version["version_id"] != row.base_profile_version_id:
            row.state = "CONFLICTED"; await _event(db, row, "CONFLICTED", {"reason": "base_version_changed"})
            await db.flush(); raise ValueError("base_version_changed")
        before = deepcopy(version["version_config"] or {})
        after = apply_manual_action(before, row.action_type, row.target_path, row.current_value, row.proposed_value)
        before_hash, after_hash = content_hash(before), content_hash(after)
        preview_payload = {
            "adjustment_id": str(row.id), "base_profile_version_id": str(row.base_profile_version_id),
            "action_type": row.action_type, "target_path": row.target_path,
            "before_hash": before_hash, "after_hash": after_hash,
            "current_value": row.current_value, "proposed_value": row.proposed_value,
        }
        row.before_config, row.after_config = before, after
        row.diff = {} if row.action_type == "OBSERVE_ONLY" else {row.target_path: {"before": row.current_value, "after": row.proposed_value}}
        row.config_hash_before, row.config_hash_after = before_hash, after_hash
        row.preview_hash = content_hash(preview_payload)
        row.state = "PENDING_MANUAL_APPROVAL"; row.previewed_at = _now(); row.updated_at = _now()
        await _event(db, row, "MANUAL_PREVIEW_GENERATED", {"preview_hash": row.preview_hash, "warnings": row.statistical_warnings or []})
        await db.flush(); return _public(row)

    async def reject(self, db: AsyncSession, user_id: UUID, adjustment_id: UUID, reason: str) -> dict[str, Any]:
        row = await self.get(db, user_id, adjustment_id)
        if row.state == "REJECTED":
            return _public(row)
        if row.state not in {"MANUAL_DRAFT", "PENDING_MANUAL_APPROVAL"}:
            raise ValueError("manual_adjustment_not_rejectable")
        row.state, row.justification, row.updated_at = "REJECTED", reason, _now()
        await _event(db, row, "REJECTED", {"reason": reason})
        await db.flush(); return _public(row)

    async def _new_champion_version(self, db: AsyncSession, row: ProfileIntelligenceManualAdjustment, config: dict[str, Any], parent_id: UUID, reason: str, rollback_to: UUID | None = None) -> tuple[UUID, UUID]:
        score = score_payload_from_profile(config); score_hash = content_hash(score)
        score_id = (await db.execute(text("""
            INSERT INTO score_engine_versions (config_hash,rules,weights,thresholds,selected_rule_ids,status)
            VALUES (:hash,CAST(:rules AS JSONB),CAST(:weights AS JSONB),CAST(:thresholds AS JSONB),CAST(:selected AS JSONB),'BASELINE')
            ON CONFLICT (config_hash) DO UPDATE SET config_hash=EXCLUDED.config_hash RETURNING id
        """), {"hash": score_hash, "rules": json.dumps(score["rules"]), "weights": json.dumps(score["weights"]), "thresholds": json.dumps(score["thresholds"]), "selected": json.dumps(score["selected_rule_ids"])})).scalar_one()
        number = int(await db.scalar(text("SELECT COALESCE(MAX(version_number),0)+1 FROM profile_versions WHERE profile_id=:id"), {"id": str(row.profile_id)}) or 1)
        version_id = uuid4()
        await db.execute(text("UPDATE profile_versions SET status='ARCHIVED',is_active=false,deactivated_at=now() WHERE profile_id=:id AND status='CHAMPION' AND is_active=true"), {"id": str(row.profile_id)})
        await db.execute(text("""
            INSERT INTO profile_versions (id,profile_id,version_number,config,mutation_reason,is_active,parent_version_id,config_hash,score_engine_version_id,status,activated_at,source_recommendation_ids,rollback_to_version_id,idempotency_key)
            VALUES (:id,:profile_id,:number,CAST(:config AS JSONB),:reason,true,:parent,:hash,:score_id,'CHAMPION',now(),'[]'::jsonb,:rollback_to,:key)
        """), {"id": str(version_id), "profile_id": str(row.profile_id), "number": number, "config": json.dumps(config), "reason": reason, "parent": str(parent_id), "hash": content_hash(config), "score_id": str(score_id), "rollback_to": str(rollback_to) if rollback_to else None, "key": f"pi-manual:{row.id}:{reason}:{content_hash(config)}"})
        await db.execute(text("UPDATE profiles SET config=CAST(:config AS JSONB),profile_version=now(),updated_at=now() WHERE id=:id AND user_id=:user_id"), {"config": json.dumps(config), "id": str(row.profile_id), "user_id": str(row.user_id)})
        return version_id, score_id

    async def approve_and_apply(self, db: AsyncSession, user_id: UUID, adjustment_id: UUID, *, preview_hash: str, justification: str, confirm_risk: bool) -> dict[str, Any]:
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:id))"), {"id": str(adjustment_id)})
        row = await self.get(db, user_id, adjustment_id)
        if row.state == "APPLIED":
            return _public(row)
        if row.state != "PENDING_MANUAL_APPROVAL":
            raise ValueError("manual_adjustment_not_pending_approval")
        if not confirm_risk:
            raise ValueError("manual_risk_confirmation_required")
        if row.preview_hash != preview_hash:
            row.state = "CONFLICTED"; await _event(db, row, "CONFLICTED", {"reason": "preview_hash_mismatch"}); await db.flush()
            raise ValueError("preview_hash_mismatch")
        version = await _eligible_version(db, user_id, row.profile_id, for_update=True)
        if version["version_id"] != row.base_profile_version_id or version["config_hash"] != row.config_hash_before:
            row.state = "CONFLICTED"; await _event(db, row, "CONFLICTED", {"reason": "stale_profile_version"}); await db.flush()
            raise ValueError("stale_profile_version")
        if content_hash(row.after_config or {}) != row.config_hash_after:
            raise ValueError("stored_preview_hash_invalid")
        row.justification, row.risk_confirmed, row.approved_by = justification, True, user_id
        row.state, row.approved_at = "MANUALLY_APPROVED", _now()
        await _event(db, row, "APPROVED", {"justification": justification, "risk_confirmed": True})
        if row.action_type == "OBSERVE_ONLY":
            row.applied_profile_version_id = row.base_profile_version_id
            row.runtime_status = "NOT_APPLICABLE"
            row.runtime_confirmation_source = "observe_only_no_mutation"
        else:
            version_id, score_id = await self._new_champion_version(db, row, row.after_config or {}, row.base_profile_version_id, "manual_profile_intelligence_apply")
            row.applied_profile_version_id = version_id
            row.runtime_target_profile_version_id = version_id
            row.runtime_target_score_engine_version_id = score_id
            row.runtime_target_config_hash = row.config_hash_after
            row.runtime_status = "RUNTIME_REFRESH_PENDING"
            row.runtime_confirmation_source = None
            row.runtime_error = None
            row.runtime_confirmed_at = None
            await db.execute(text("""
                INSERT INTO profile_audit_log (id,user_id,profile_id,changed_by,change_source,change_description,previous_config,new_config,previous_profile_version,new_profile_version,created_at)
                VALUES (:id,:user_id,:profile_id,:user_id,'MANUAL_PROFILE_INTELLIGENCE',:description,CAST(:before AS JSONB),CAST(:after AS JSONB),:previous_profile_version,now(),now())
            """), {"id": str(uuid4()), "user_id": str(user_id), "profile_id": str(row.profile_id), "description": f"manual adjustment {row.id}: {justification}", "before": json.dumps(row.before_config), "after": json.dumps(row.after_config), "previous_profile_version": version["profile_version"]})
        row.state, row.applied_at, row.updated_at = "APPLIED", _now(), _now()
        await _event(db, row, "APPLIED_DB", {"profile_version_id": str(row.applied_profile_version_id), "score_engine_version_id": str(row.runtime_target_score_engine_version_id) if row.runtime_target_score_engine_version_id else None, "runtime_status": row.runtime_status, "mutation_source": row.mutation_source, "autopilot_applied": False, "ml_training_mutated": False, "historical_dataset_mutated": False})
        await db.flush(); return _public(row)

    async def rollback(self, db: AsyncSession, user_id: UUID, adjustment_id: UUID, reason: str) -> dict[str, Any]:
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:id))"), {"id": str(adjustment_id)})
        row = await self.get(db, user_id, adjustment_id)
        if row.state == "ROLLED_BACK":
            return _public(row)
        if row.state != "APPLIED" or not row.applied_profile_version_id:
            raise ValueError("only_applied_adjustment_can_rollback")
        if row.action_type != "OBSERVE_ONLY" and row.runtime_status != "RUNTIME_CONFIRMED":
            raise ValueError("runtime_confirmation_required_before_rollback")
        current = await _eligible_version(db, user_id, row.profile_id, for_update=True)
        if current["version_id"] != row.applied_profile_version_id:
            row.state = "CONFLICTED"; await _event(db, row, "CONFLICTED", {"reason": "rollback_current_version_changed"}); await db.flush()
            raise ValueError("rollback_current_version_changed")
        if row.action_type == "OBSERVE_ONLY":
            rollback_id = row.base_profile_version_id
            rollback_score_id = None
        else:
            rollback_id, rollback_score_id = await self._new_champion_version(db, row, row.before_config or {}, row.applied_profile_version_id, "manual_profile_intelligence_rollback", row.base_profile_version_id)
            row.runtime_target_profile_version_id = rollback_id
            row.runtime_target_score_engine_version_id = rollback_score_id
            row.runtime_target_config_hash = row.config_hash_before
            row.runtime_status = "RUNTIME_REFRESH_PENDING"
            row.runtime_confirmation_source = None
            row.runtime_error = None
            row.runtime_confirmed_at = None
        row.rollback_profile_version_id, row.rollback_reason = rollback_id, reason
        row.state, row.rolled_back_at, row.updated_at = "ROLLED_BACK", _now(), _now()
        await _event(db, row, "ROLLED_BACK", {"rollback_profile_version_id": str(rollback_id), "reason": reason})
        await db.flush(); return _public(row)


async def confirm_manual_runtime_profiles(
    db: AsyncSession,
    runtime_snapshots: Mapping[UUID, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Confirm only versions/configs actually loaded by the production L3 scanner.

    The scanner calls this immediately after its profile snapshot query.  This
    never changes a profile, watchlist, score rule, dataset, or cache.
    """
    if not runtime_snapshots:
        return []
    rows: list[Mapping[str, Any]] = []
    for profile_id, snapshot in runtime_snapshots.items():
        profile_version_id = snapshot.get("profile_version_id")
        score_engine_version_id = snapshot.get("score_engine_version_id")
        config_hash = snapshot.get("config_hash")
        if not profile_version_id or not score_engine_version_id or not config_hash:
            continue
        if content_hash(snapshot.get("config") or {}) != config_hash:
            continue
        confirmed = (await db.execute(text("""
            UPDATE profile_intelligence_manual_adjustments
               SET runtime_status = 'RUNTIME_CONFIRMED',
                   runtime_confirmed_at = now(),
                   runtime_confirmation_source = 'pipeline_scan_profile_snapshot',
                   runtime_error = NULL,
                   updated_at = now()
             WHERE profile_id = CAST(:profile_id AS UUID)
               AND runtime_status = 'RUNTIME_REFRESH_PENDING'
               AND runtime_target_profile_version_id = CAST(:profile_version_id AS UUID)
               AND runtime_target_score_engine_version_id = CAST(:score_engine_version_id AS UUID)
               AND runtime_target_config_hash = :config_hash
            RETURNING id, profile_id, runtime_target_profile_version_id AS profile_version_id,
                      runtime_target_score_engine_version_id AS score_engine_version_id,
                      runtime_target_config_hash AS config_hash
        """), {
            "profile_id": str(profile_id),
            "profile_version_id": str(profile_version_id),
            "score_engine_version_id": str(score_engine_version_id),
            "config_hash": config_hash,
        })).mappings().all()
        rows.extend(confirmed)
    for item in rows:
        adjustment = await db.get(ProfileIntelligenceManualAdjustment, item["id"])
        if adjustment:
            await _event(db, adjustment, "RUNTIME_CONFIRMED", {
                "source": "pipeline_scan_profile_snapshot",
                "profile_version_id": str(item["profile_version_id"]),
                "score_engine_version_id": str(item["score_engine_version_id"]),
                "config_hash": item["config_hash"],
            })
    return [dict(item) for item in rows]


profile_intelligence_manual_service = ProfileIntelligenceManualService()
public_manual_adjustment = _public
