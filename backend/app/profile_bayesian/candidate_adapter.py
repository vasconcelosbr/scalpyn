"""Adapter from Bayesian drafts to the existing immutable shadow workflow."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile import Profile
from ..models.profile_intelligence_autopilot import ProfileIntelligenceAutopilotCycle
from ..services.calibration_orchestrator_v2 import (
    apply_stable_patch,
    resolve_stable_path,
)
from ..services.profile_intelligence_autopilot_service import (
    ProfileIntelligenceAutopilotService,
)
from .audit import record_event
from .config import BayesianPolicy, feature_flags
from .metrics import CANDIDATES_GENERATED, increment
from .schemas import CandidateChange
from .validation.profile_replay_adapter import ProfileReplayAdapter


SOURCE = "PROFILE_BAYESIAN_INTELLIGENCE"


def _content_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


class CandidateAdapter:
    async def create_draft(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        profile_id: UUID,
        analysis_run_id: UUID,
        optimization_study_id: UUID | None,
        base_profile_version_id: UUID | None,
        changes: Sequence[CandidateChange],
        evidence: Mapping[str, Any],
        idempotency_key: str,
        policy: BayesianPolicy,
    ) -> Mapping[str, Any]:
        flags = feature_flags()
        if not flags.enabled or not flags.candidate_creation_enabled:
            raise PermissionError("PROFILE_BAYESIAN_CANDIDATE_CREATION_ENABLED is false")
        policy.require_permission("profile_bayesian.create_candidate")
        if not changes:
            raise ValueError("candidate must contain at least one change")
        if len(changes) > policy.int("max_changes_per_candidate"):
            raise ValueError("candidate exceeds configured change limit")
        if any(item.source_analysis_run_id != analysis_run_id for item in changes):
            raise ValueError("candidate change lineage does not match analysis run")
        analysis = (
            await db.execute(
                text(
                    """
                    SELECT status, diagnostic_status, dataset_snapshot_id
                    FROM profile_bayesian_analysis_runs
                    WHERE id = :id AND user_id = :user_id AND profile_id = :profile_id
                    """
                ),
                {
                    "id": str(analysis_run_id),
                    "user_id": str(user_id),
                    "profile_id": str(profile_id),
                },
            )
        ).mappings().first()
        if not analysis or analysis["status"] not in {
            "COMPLETED",
            "COMPLETED_WITH_WARNINGS",
        } or analysis["diagnostic_status"] not in {"VALID", "VALID_WITH_WARNINGS"}:
            raise ValueError("analysis is not eligible for candidate creation")
        if optimization_study_id:
            study = (
                await db.execute(
                    text(
                        """
                        SELECT status, valid_trials
                        FROM profile_optimization_studies
                        WHERE id=:id AND user_id=:user_id AND profile_id=:profile_id
                          AND analysis_run_id=:analysis_run_id
                        """
                    ),
                    {
                        "id": str(optimization_study_id),
                        "user_id": str(user_id),
                        "profile_id": str(profile_id),
                        "analysis_run_id": str(analysis_run_id),
                    },
                )
            ).mappings().first()
            if not study or study["status"] != "COMPLETED" or study["valid_trials"] < 1:
                raise ValueError("optimization study has no eligible valid trial")
        candidate_count = int(
            await db.scalar(
                text(
                    """
                    SELECT count(*) FROM profile_bayesian_candidate_links
                    WHERE analysis_run_id=:analysis_run_id
                    """
                ),
                {"analysis_run_id": str(analysis_run_id)},
            )
            or 0
        )
        if candidate_count >= policy.int("max_candidates"):
            raise ValueError("analysis reached configured candidate limit")
        profile = await db.get(Profile, profile_id)
        if not profile or profile.user_id != user_id:
            raise ValueError("profile not found")
        base_config: Mapping[str, Any] = profile.config or {}
        if base_profile_version_id:
            version = (
                await db.execute(
                    text(
                        """
                        SELECT config FROM profile_versions
                        WHERE id=:id AND profile_id=:profile_id
                        """
                    ),
                    {
                        "id": str(base_profile_version_id),
                        "profile_id": str(profile_id),
                    },
                )
            ).mappings().first()
            if not version:
                raise ValueError("base profile version not found")
            base_config = version["config"] or {}
        authorized = policy.values["authorized_search_space"]
        for change in changes:
            limits = authorized.get(change.target_path)
            if not isinstance(limits, Mapping):
                raise ValueError(f"unauthorized candidate path: {change.target_path}")
            actual_current = resolve_stable_path(base_config, change.target_path)
            if actual_current != change.current_value:
                raise ValueError(f"current value mismatch: {change.target_path}")
            if isinstance(change.candidate_value, bool) or not isinstance(
                change.candidate_value, (int, float)
            ):
                raise ValueError(f"candidate value must be numeric: {change.target_path}")
            candidate_value = float(change.candidate_value)
            current_value = float(actual_current)
            if not float(limits["min"]) <= candidate_value <= float(limits["max"]):
                raise ValueError(f"candidate value outside policy: {change.target_path}")
            if abs(candidate_value - current_value) > float(
                limits["max_absolute_delta"]
            ):
                raise ValueError(f"candidate delta outside policy: {change.target_path}")
        effect_ids = [UUID(str(item)) for item in evidence.get("effect_ids") or []]
        if not effect_ids:
            raise ValueError("candidate requires persisted indicator effect evidence")
        valid_effects = int(
            await db.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM profile_bayesian_indicator_effects
                    WHERE analysis_run_id=:analysis_run_id
                      AND id IN (
                        SELECT value::uuid
                        FROM jsonb_array_elements_text(CAST(:ids AS JSONB))
                      )
                      AND evidence_grade IN ('STRONG', 'VERY_STRONG')
                      AND diagnostic_status IN ('VALID', 'VALID_WITH_WARNINGS')
                    """
                ),
                {
                    "analysis_run_id": str(analysis_run_id),
                    "ids": json.dumps([str(item) for item in effect_ids]),
                },
            )
            or 0
        )
        if valid_effects != len(set(effect_ids)):
            raise ValueError("candidate evidence is not strong or not in analysis scope")
        existing = (
            await db.execute(
                text(
                    """
                    SELECT * FROM profile_bayesian_candidate_links
                    WHERE idempotency_key = :key AND user_id = :user_id
                    """
                ),
                {"key": idempotency_key, "user_id": str(user_id)},
            )
        ).mappings().first()
        if existing:
            return dict(existing)
        candidate_id = uuid4()
        change_payload = [item.model_dump(mode="json") for item in changes]
        await db.execute(
            text(
                """
                INSERT INTO profile_bayesian_candidate_links (
                    id, user_id, profile_id, base_profile_version_id,
                    analysis_run_id, optimization_study_id, source, status,
                    changes, evidence, validation_metrics, shadow_metrics,
                    approval_status, idempotency_key
                ) VALUES (
                    :id, :user_id, :profile_id, :base_version_id,
                    :analysis_run_id, :study_id, :source, 'DRAFT',
                    CAST(:changes AS JSONB), CAST(:evidence AS JSONB),
                    '{}'::jsonb, '{}'::jsonb, 'pending', :idempotency_key
                )
                """
            ),
            {
                "id": str(candidate_id),
                "user_id": str(user_id),
                "profile_id": str(profile_id),
                "base_version_id": (
                    str(base_profile_version_id) if base_profile_version_id else None
                ),
                "analysis_run_id": str(analysis_run_id),
                "study_id": (
                    str(optimization_study_id) if optimization_study_id else None
                ),
                "source": SOURCE,
                "changes": json.dumps(change_payload, default=str),
                "evidence": json.dumps(dict(evidence), default=str),
                "idempotency_key": idempotency_key,
            },
        )
        await record_event(
            db,
            user_id=user_id,
            actor_user_id=user_id,
            profile_id=profile_id,
            analysis_run_id=analysis_run_id,
            study_id=optimization_study_id,
            candidate_link_id=candidate_id,
            event_type="CANDIDATE_DRAFT_CREATED",
            new_status="DRAFT",
            payload={
                "source": SOURCE,
                "change_count": len(changes),
                "automatic_activation": False,
            },
        )
        await db.commit()
        increment(CANDIDATES_GENERATED)
        return {
            "id": str(candidate_id),
            "status": "DRAFT",
            "source": SOURCE,
            "changes": change_payload,
        }

    async def submit_replay(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        candidate_id: UUID,
        expected_status: str,
        policy: BayesianPolicy,
    ) -> Mapping[str, Any]:
        policy.require_permission("profile_bayesian.submit_replay")
        candidate = await self._lock_candidate(db, user_id, candidate_id)
        if candidate["status"] != expected_status or expected_status not in {
            "DRAFT",
            "ANALYZED",
            "REPLAY_FAILED",
        }:
            raise ValueError("candidate state transition conflict")
        profile = await db.get(Profile, candidate["profile_id"])
        if not profile or profile.user_id != user_id:
            raise ValueError("profile not found")
        candidate_config = self._apply_changes(profile.config or {}, candidate["changes"])
        result = await ProfileReplayAdapter().run(
            base_profile_config=profile.config or {},
            candidate_config=candidate_config,
            dataset_hash=str((candidate["evidence"] or {}).get("dataset_hash") or ""),
        )
        await db.execute(
            text(
                """
                UPDATE profile_bayesian_candidate_links
                SET status = :status,
                    validation_metrics = CAST(:metrics AS JSONB),
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(candidate_id),
                "status": result.status,
                "metrics": json.dumps(
                    {
                        **dict(result.metrics),
                        "supported": result.supported,
                        "reason": result.reason,
                        "operational_mutation": result.operational_mutation,
                        "orders_created": result.orders_created,
                    }
                ),
            },
        )
        await record_event(
            db,
            user_id=user_id,
            actor_user_id=user_id,
            profile_id=candidate["profile_id"],
            analysis_run_id=candidate["analysis_run_id"],
            candidate_link_id=candidate_id,
            event_type="REPLAY_COMPLETED",
            previous_status=candidate["status"],
            new_status=result.status,
            payload={"supported": result.supported, "reason": result.reason},
        )
        await db.commit()
        return {
            "id": str(candidate_id),
            "status": result.status,
            "supported": result.supported,
            "reason": result.reason,
        }

    async def submit_shadow(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        candidate_id: UUID,
        expected_status: str,
        policy: BayesianPolicy,
    ) -> Mapping[str, Any]:
        flags = feature_flags()
        if not flags.enabled or not flags.shadow_submission_enabled:
            raise PermissionError("PROFILE_BAYESIAN_SHADOW_SUBMISSION_ENABLED is false")
        policy.require_permission("profile_bayesian.submit_shadow")
        candidate = await self._lock_candidate(db, user_id, candidate_id)
        if candidate["status"] != expected_status or expected_status != "VALIDATED":
            raise ValueError("only a replay-validated candidate may enter shadow")
        base_profile = await db.get(Profile, candidate["profile_id"])
        if not base_profile or base_profile.user_id != user_id:
            raise ValueError("profile not found")
        config = self._apply_changes(base_profile.config or {}, candidate["changes"])
        cycle_key = f"profile-bayesian-shadow:{candidate_id}"
        cycle = (
            await db.execute(
                text(
                    """
                    SELECT id FROM profile_intelligence_autopilot_cycles
                    WHERE idempotency_key = :key
                    """
                ),
                {"key": cycle_key},
            )
        ).scalar_one_or_none()
        if cycle:
            cycle_row = await db.get(ProfileIntelligenceAutopilotCycle, cycle)
        else:
            cycle_row = ProfileIntelligenceAutopilotCycle(
                id=uuid4(),
                user_id=user_id,
                window_start=datetime.now(timezone.utc),
                idempotency_key=cycle_key,
                status="RUNNING",
                checkpoint="PROFILE_BAYESIAN_SHADOW_SUBMISSION",
                metrics_json={},
                errors_json=[],
            )
            db.add(cycle_row)
            await db.flush()
        autopilot = ProfileIntelligenceAutopilotService()
        _, settings = await autopilot.get_settings(db, user_id)
        metrics = {
            "created": 0,
            "cooldown_blocked": 0,
            "deduplicated": 0,
            "disabled_for_capacity": 0,
        }
        existing_candidate = await autopilot.create_candidate_from_calibration_proposal(
            db,
            user_id=user_id,
            cycle=cycle_row,
            settings=settings,
            metrics=metrics,
            base_profile=base_profile,
            config=config,
            evidence={
                **dict(candidate["evidence"] or {}),
                "source": SOURCE,
                "bayesian_candidate_link_id": str(candidate_id),
                "analysis_authority_only": True,
            },
        )
        if existing_candidate is None:
            raise ValueError("existing shadow candidate workflow rejected candidate")
        await db.execute(
            text(
                """
                UPDATE profile_bayesian_candidate_links
                SET autopilot_candidate_id = :autopilot_candidate_id,
                    status = 'SHADOW_RUNNING',
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(candidate_id),
                "autopilot_candidate_id": str(existing_candidate.id),
            },
        )
        cycle_row.status = "COMPLETED"
        cycle_row.completed_at = datetime.now(timezone.utc)
        await record_event(
            db,
            user_id=user_id,
            actor_user_id=user_id,
            profile_id=candidate["profile_id"],
            analysis_run_id=candidate["analysis_run_id"],
            candidate_link_id=candidate_id,
            event_type="SHADOW_SUBMITTED",
            previous_status="VALIDATED",
            new_status="SHADOW_RUNNING",
            payload={
                "autopilot_candidate_id": str(existing_candidate.id),
                "profile_mutation": False,
                "live_activation": False,
            },
        )
        await db.commit()
        return {
            "id": str(candidate_id),
            "status": "SHADOW_RUNNING",
            "autopilot_candidate_id": str(existing_candidate.id),
        }

    async def _lock_candidate(
        self, db: AsyncSession, user_id: UUID, candidate_id: UUID
    ) -> Mapping[str, Any]:
        row = (
            await db.execute(
                text(
                    """
                    SELECT * FROM profile_bayesian_candidate_links
                    WHERE id = :id AND user_id = :user_id
                    FOR UPDATE
                    """
                ),
                {"id": str(candidate_id), "user_id": str(user_id)},
            )
        ).mappings().first()
        if not row:
            raise ValueError("candidate not found")
        return row

    @staticmethod
    def _apply_changes(
        base_config: Mapping[str, Any], changes: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        config = deepcopy(dict(base_config))
        for change in changes:
            config = apply_stable_patch(
                config,
                str(change["target_path"]),
                change["candidate_value"],
            )
        return config
