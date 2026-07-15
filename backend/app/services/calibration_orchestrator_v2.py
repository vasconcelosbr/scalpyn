"""Fail-closed bridge from structured evidence to a shadow challenger.

The orchestrator never mutates an incumbent profile.  It creates immutable
Recommendation/Proposal records and delegates challenger creation to the
existing Profile Intelligence shadow-candidate workflow.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile import Profile
from ..models.profile_intelligence_autopilot import ProfileIntelligenceAutopilotCycle


ALLOWED_RECOMMENDATION_TYPES = {
    "ADD_BLOCK_RULE",
    "UPDATE_THRESHOLD",
    "UPDATE_WEIGHT",
    "REMOVE_RULE",
}
ALLOWED_RISKS = {"LOW", "MEDIUM", "HIGH"}


def content_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _path_parts(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValueError("target_path_must_be_absolute")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]
    if not parts or any(not part for part in parts):
        raise ValueError("invalid_target_path")
    return parts


def resolve_stable_path(config: Mapping[str, Any], path: str) -> Any:
    current: Any = config
    parts = _path_parts(path)
    index = 0
    while index < len(parts):
        part = parts[index]
        if part == "by_id":
            if not isinstance(current, list) or index + 1 >= len(parts):
                raise ValueError("invalid_by_id_path")
            stable_id = parts[index + 1]
            current = next((item for item in current if isinstance(item, dict) and (
                item.get("condition_id") == stable_id
                or item.get("rule_id") == stable_id
                or item.get("id") == stable_id
            )), None)
            if current is None:
                raise ValueError("stable_id_not_found")
            index += 2
            continue
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError("target_path_not_found")
        current = current[part]
        index += 1
    return current


def apply_stable_patch(config: Mapping[str, Any], path: str, proposed_value: Any) -> dict:
    result = deepcopy(dict(config))
    parts = _path_parts(path)
    current: Any = result
    index = 0
    while index < len(parts) - 1:
        part = parts[index]
        if part == "by_id":
            if not isinstance(current, list) or index + 1 >= len(parts) - 1:
                raise ValueError("invalid_by_id_path")
            stable_id = parts[index + 1]
            current = next((item for item in current if isinstance(item, dict) and (
                item.get("condition_id") == stable_id
                or item.get("rule_id") == stable_id
                or item.get("id") == stable_id
            )), None)
            if current is None:
                raise ValueError("stable_id_not_found")
            index += 2
            continue
        if not isinstance(current, dict) or part not in current:
            raise ValueError("target_path_not_found")
        current = current[part]
        index += 1
    leaf = parts[-1]
    if not isinstance(current, dict) or leaf not in current:
        raise ValueError("target_path_not_found")
    current[leaf] = proposed_value
    return result


def apply_recommendation_patch(
    config: Mapping[str, Any], recommendation_type: str, path: str, proposed_value: Any
) -> dict:
    """Apply a bounded mutation using stable IDs, never list indexes."""
    if recommendation_type == "ADD_BLOCK_RULE":
        result = deepcopy(dict(config))
        target = resolve_stable_path(result, path)
        if not isinstance(target, list) or not isinstance(proposed_value, dict):
            raise ValueError("add_block_rule_requires_list_and_object")
        stable_id = (
            proposed_value.get("condition_id")
            or proposed_value.get("rule_id")
            or proposed_value.get("id")
        )
        if not stable_id:
            raise ValueError("added_rule_requires_stable_id")
        if any(
            isinstance(item, dict)
            and stable_id in {item.get("condition_id"), item.get("rule_id"), item.get("id")}
            for item in target
        ):
            raise ValueError("stable_id_already_exists")
        target.append(deepcopy(proposed_value))
        return result
    if recommendation_type == "REMOVE_RULE":
        parts = _path_parts(path)
        if len(parts) < 3 or parts[-2] != "by_id":
            raise ValueError("remove_rule_requires_stable_id_path")
        stable_id = parts[-1]
        container_path = "/" + "/".join(parts[:-2])
        result = deepcopy(dict(config))
        target = resolve_stable_path(result, container_path)
        if not isinstance(target, list):
            raise ValueError("remove_rule_target_not_list")
        before = len(target)
        target[:] = [
            item for item in target
            if not (
                isinstance(item, dict)
                and stable_id in {item.get("condition_id"), item.get("rule_id"), item.get("id")}
            )
        ]
        if len(target) == before:
            raise ValueError("stable_id_not_found")
        return result
    return apply_stable_patch(config, path, proposed_value)


async def _record_state(
    db: AsyncSession,
    *,
    user_id: UUID,
    profile_id: UUID,
    new_state: str,
    actor: str,
    reason: str,
    previous_state: str | None = None,
    cycle_id: UUID | None = None,
    recommendation_id: UUID | None = None,
    proposal_id: UUID | None = None,
    metrics: Mapping[str, Any] | None = None,
    artifact_refs: Sequence[str] | None = None,
) -> UUID:
    event_id = uuid4()
    await db.execute(text("""
        INSERT INTO calibration_state_events (
            id, user_id, cycle_id, profile_id, recommendation_id, proposal_id,
            previous_state, new_state, actor, reason, metrics, artifact_refs
        ) VALUES (
            :id, :user_id, :cycle_id, :profile_id, :recommendation_id, :proposal_id,
            :previous_state, :new_state, :actor, :reason,
            CAST(:metrics AS JSONB), CAST(:artifact_refs AS JSONB)
        )
    """), {
        "id": str(event_id),
        "user_id": str(user_id),
        "cycle_id": str(cycle_id) if cycle_id else None,
        "profile_id": str(profile_id),
        "recommendation_id": str(recommendation_id) if recommendation_id else None,
        "proposal_id": str(proposal_id) if proposal_id else None,
        "previous_state": previous_state,
        "new_state": new_state,
        "actor": actor,
        "reason": reason,
        "metrics": json.dumps(metrics or {}, default=str),
        "artifact_refs": json.dumps(list(artifact_refs or []), default=str),
    })
    return event_id


class CalibrationOrchestratorV2:
    async def create_recommendation(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        profile_id: UUID,
        base_profile_version_id: UUID,
        recommendation_type: str,
        target_path: str,
        current_value: Any,
        proposed_value: Any,
        bounded_change: Mapping[str, Any],
        evidence_refs: Sequence[UUID],
        expected_impact: Mapping[str, Any],
        risk: str,
        confidence: float,
        validation_required: str,
        rollback_condition: str,
        cycle_id: UUID | None = None,
        actor: str = "profile_intelligence",
    ) -> dict[str, Any]:
        if recommendation_type not in ALLOWED_RECOMMENDATION_TYPES:
            raise ValueError("unsupported_recommendation_type")
        if risk not in ALLOWED_RISKS:
            raise ValueError("unsupported_risk")
        if not 0 <= float(confidence) <= 1:
            raise ValueError("confidence_out_of_range")
        if not bounded_change.get("within_policy"):
            raise ValueError("bounded_change_outside_policy")
        if "net_ev_delta_pct" not in expected_impact:
            raise ValueError("missing_expected_net_ev_delta_pct")
        if not rollback_condition.strip():
            raise ValueError("missing_rollback_condition")
        if not evidence_refs:
            raise ValueError("missing_evidence_refs")

        profile = await db.get(Profile, profile_id)
        if not profile or profile.user_id != user_id:
            raise ValueError("profile_not_found")
        version = (await db.execute(text("""
            SELECT id, config, config_hash, status
              FROM profile_versions
             WHERE id = :version_id AND profile_id = :profile_id
        """), {
            "version_id": str(base_profile_version_id),
            "profile_id": str(profile_id),
        })).mappings().first()
        if not version or version["status"] != "CHAMPION":
            raise ValueError("base_version_not_champion")
        actual_current = resolve_stable_path(version["config"] or {}, target_path)
        if actual_current != current_value:
            raise ValueError("current_value_mismatch")

        evidence_rows = (await db.execute(text("""
            SELECT evidence_id, source_type, profile_id, profile_version_id, status
              FROM ml_evidence_registry
             WHERE evidence_id IN (
                 SELECT value::uuid
                   FROM jsonb_array_elements_text(CAST(:ids AS jsonb))
             )
        """), {"ids": json.dumps([str(item) for item in evidence_refs])})).mappings().all()
        if len(evidence_rows) != len(set(evidence_refs)):
            raise ValueError("evidence_not_found")
        if any(
            row["status"] != "VALID"
            or row["profile_id"] != profile_id
            or row["profile_version_id"] != base_profile_version_id
            for row in evidence_rows
        ):
            raise ValueError("evidence_scope_or_status_invalid")
        sources = {row["source_type"] for row in evidence_rows}
        if "MATH" not in sources or not sources.intersection({"ML", "OPTUNA"}):
            raise ValueError("evidence_consensus_not_met")

        payload = {
            "profile_id": str(profile_id),
            "base_profile_version_id": str(base_profile_version_id),
            "recommendation_type": recommendation_type,
            "target_path": target_path,
            "current_value": current_value,
            "proposed_value": proposed_value,
            "evidence_refs": sorted(str(item) for item in evidence_refs),
        }
        key = f"recommendation-v2:{content_hash(payload)}"
        existing = (await db.execute(text("""
            SELECT id, status FROM calibration_recommendations
             WHERE idempotency_key = :key
        """), {"key": key})).mappings().first()
        if existing:
            return {"id": str(existing["id"]), "status": existing["status"], "created": False}

        recommendation_id = uuid4()
        await db.execute(text("""
            INSERT INTO calibration_recommendations (
                id, user_id, cycle_id, profile_id, base_profile_version_id,
                recommendation_type, target_path, current_value, proposed_value,
                bounded_change, evidence_refs, expected_impact, risk, confidence,
                validation_required, rollback_condition, status, idempotency_key
            ) VALUES (
                :id, :user_id, :cycle_id, :profile_id, :base_profile_version_id,
                :recommendation_type, :target_path, CAST(:current_value AS JSONB),
                CAST(:proposed_value AS JSONB), CAST(:bounded_change AS JSONB),
                CAST(:evidence_refs AS JSONB), CAST(:expected_impact AS JSONB),
                :risk, :confidence, :validation_required, :rollback_condition,
                'PROPOSED', :idempotency_key
            )
        """), {
            "id": str(recommendation_id), "user_id": str(user_id),
            "cycle_id": str(cycle_id) if cycle_id else None,
            "profile_id": str(profile_id),
            "base_profile_version_id": str(base_profile_version_id),
            "recommendation_type": recommendation_type,
            "target_path": target_path,
            "current_value": json.dumps(current_value, default=str),
            "proposed_value": json.dumps(proposed_value, default=str),
            "bounded_change": json.dumps(dict(bounded_change), default=str),
            "evidence_refs": json.dumps([str(item) for item in evidence_refs]),
            "expected_impact": json.dumps(dict(expected_impact), default=str),
            "risk": risk, "confidence": confidence,
            "validation_required": validation_required,
            "rollback_condition": rollback_condition,
            "idempotency_key": key,
        })
        await _record_state(
            db, user_id=user_id, profile_id=profile_id, cycle_id=cycle_id,
            recommendation_id=recommendation_id, new_state="PROPOSED",
            actor=actor, reason="structured_evidence_consensus_passed",
            metrics={"confidence": confidence, "expected_impact": expected_impact},
            artifact_refs=[str(item) for item in evidence_refs],
        )
        return {"id": str(recommendation_id), "status": "PROPOSED", "created": True}

    async def create_shadow_proposal(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        recommendation_id: UUID,
        actor: str = "calibration_orchestrator_v2",
    ) -> dict[str, Any]:
        recommendation = (await db.execute(text("""
            SELECT r.*, p.name AS profile_name, pv.config AS base_config
              FROM calibration_recommendations r
              JOIN profiles p ON p.id = r.profile_id
              JOIN profile_versions pv ON pv.id = r.base_profile_version_id
             WHERE r.id = :id AND r.user_id = :user_id
             FOR UPDATE OF r
        """), {"id": str(recommendation_id), "user_id": str(user_id)})).mappings().first()
        if not recommendation:
            raise ValueError("recommendation_not_found")
        existing = (await db.execute(text("""
            SELECT id, state, autopilot_candidate_id FROM calibration_proposals
             WHERE recommendation_id = :recommendation_id
        """), {"recommendation_id": str(recommendation_id)})).mappings().first()
        if existing:
            return {
                "id": str(existing["id"]), "state": existing["state"],
                "candidate_id": str(existing["autopilot_candidate_id"]) if existing["autopilot_candidate_id"] else None,
                "created": False,
            }
        if recommendation["status"] != "PROPOSED":
            raise ValueError("recommendation_not_proposed")

        after_config = apply_recommendation_patch(
            recommendation["base_config"] or {},
            recommendation["recommendation_type"],
            recommendation["target_path"],
            recommendation["proposed_value"],
        )
        cycle_key = f"calibration-v2:{recommendation_id}"
        cycle = await db.scalar(select(ProfileIntelligenceAutopilotCycle).where(
            ProfileIntelligenceAutopilotCycle.idempotency_key == cycle_key
        ))
        if not cycle:
            now = datetime.now(timezone.utc)
            cycle = ProfileIntelligenceAutopilotCycle(
                id=uuid4(), user_id=user_id, window_start=now,
                idempotency_key=cycle_key, status="PROPOSED",
                checkpoint="CALIBRATION_PROPOSAL", metrics_json={}, errors_json=[],
            )
            db.add(cycle)
            await db.flush()
        profile = await db.get(Profile, recommendation["profile_id"])
        if not profile:
            raise ValueError("profile_not_found")
        from .profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService
        autopilot = ProfileIntelligenceAutopilotService()
        _, settings = await autopilot.get_settings(db, user_id)
        metrics = {
            "created": 0, "cooldown_blocked": 0, "deduplicated": 0,
            "disabled_for_capacity": 0,
        }
        candidate = await autopilot.create_candidate_from_calibration_proposal(
            db,
            user_id=user_id,
            cycle=cycle,
            settings=settings,
            metrics=metrics,
            base_profile=profile,
            config=after_config,
            evidence={
                "recommendation_id": str(recommendation_id),
                "evidence_refs": recommendation["evidence_refs"],
                "expected_impact": recommendation["expected_impact"],
                "rollback_condition": recommendation["rollback_condition"],
            },
        )
        if candidate is None:
            raise ValueError("shadow_candidate_not_created")
        candidate_evidence = candidate.evidence_json or {}
        challenger_version_id = candidate_evidence.get("profile_version_id")
        proposal_id = uuid4()
        diff = {
            recommendation["target_path"]: {
                "before": recommendation["current_value"],
                "after": recommendation["proposed_value"],
            }
        }
        await db.execute(text("""
            INSERT INTO calibration_proposals (
                id, recommendation_id, user_id, profile_id, base_profile_version_id,
                challenger_profile_id, challenger_profile_version_id,
                autopilot_candidate_id, state, before_config, after_config, diff,
                expected_impact, idempotency_key
            ) VALUES (
                :id, :recommendation_id, :user_id, :profile_id, :base_version_id,
                :challenger_profile_id, :challenger_version_id, :candidate_id,
                'SHADOW_CANARY', CAST(:before_config AS JSONB), CAST(:after_config AS JSONB),
                CAST(:diff AS JSONB), CAST(:expected_impact AS JSONB), :idempotency_key
            )
        """), {
            "id": str(proposal_id), "recommendation_id": str(recommendation_id),
            "user_id": str(user_id), "profile_id": str(recommendation["profile_id"]),
            "base_version_id": str(recommendation["base_profile_version_id"]),
            "challenger_profile_id": str(candidate.profile_id),
            "challenger_version_id": challenger_version_id,
            "candidate_id": str(candidate.id),
            "before_config": json.dumps(recommendation["base_config"], default=str),
            "after_config": json.dumps(after_config, default=str),
            "diff": json.dumps(diff, default=str),
            "expected_impact": json.dumps(recommendation["expected_impact"], default=str),
            "idempotency_key": f"proposal-v2:{recommendation_id}",
        })
        await db.execute(text("""
            UPDATE calibration_recommendations
               SET status = 'VALIDATING', updated_at = now()
             WHERE id = :id
        """), {"id": str(recommendation_id)})
        await _record_state(
            db, user_id=user_id, profile_id=recommendation["profile_id"],
            cycle_id=cycle.id, recommendation_id=recommendation_id,
            proposal_id=proposal_id, previous_state="PROPOSED",
            new_state="SHADOW_CANARY", actor=actor,
            reason="immutable_shadow_challenger_created",
            artifact_refs=[str(candidate.id), str(candidate.profile_id), str(challenger_version_id)],
        )
        return {
            "id": str(proposal_id), "state": "SHADOW_CANARY",
            "candidate_id": str(candidate.id),
            "challenger_profile_id": str(candidate.profile_id),
            "challenger_profile_version_id": str(challenger_version_id),
            "created": True,
        }


calibration_orchestrator_v2 = CalibrationOrchestratorV2()
