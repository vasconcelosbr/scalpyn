from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.copilot import CopilotActionPlan, CopilotAuditLog
from ..models.profile import Profile
from ..models.profile_audit_log import ProfileAuditLog


APPROVAL_TEXTS = {"CONFIRMO EXECUTAR", "APROVADO, EXECUTAR"}


def profile_state_hash(profile: Profile) -> str:
    payload = {
        "id": str(profile.id), "config": profile.config or {},
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        "profile_version": profile.profile_version.isoformat() if profile.profile_version else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _path_parts(path: str) -> list[str | int]:
    if path.startswith(".") or path.endswith(".") or ".." in path:
        raise ValueError(f"Path inválido: {path}")
    return [int(part) if part.isdigit() else part for part in path.split(".")]


def _read_path(document: Any, parts: list[str | int]) -> Any:
    cursor = document
    for part in parts:
        if isinstance(part, int):
            if not isinstance(cursor, list) or part >= len(cursor):
                raise ValueError(f"Índice inexistente no path: {part}")
        elif not isinstance(cursor, dict) or part not in cursor:
            raise ValueError(f"Campo inexistente no path: {part}")
        cursor = cursor[part]
    return cursor


def apply_changes(config: dict[str, Any], changes: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidate = deepcopy(config)
    normalized = []
    for change in changes:
        parts = _path_parts(change["path"])
        old_value = _read_path(candidate, parts)
        supplied_old = change.get("old_value")
        if supplied_old is not None and old_value != supplied_old:
            raise ValueError(f"Estado divergente em {change['path']}: valor atual não corresponde ao DRY_RUN solicitado")
        parent = candidate
        for part in parts[:-1]:
            parent = parent[part]
        parent[parts[-1]] = deepcopy(change["new_value"])
        normalized.append({
            "path": change["path"], "old_value": old_value,
            "new_value": change["new_value"], "reason": change["reason"],
        })
    return candidate, normalized


def action_to_dict(plan: CopilotActionPlan) -> dict[str, Any]:
    return {
        "id": str(plan.id), "mode": "DRY_RUN" if plan.status == "DRY_RUN" else plan.status,
        "requires_human_approval": plan.status == "DRY_RUN",
        "action_type": plan.action_type, "target_type": plan.target_type,
        "target_id": plan.target_id, "profile_id": plan.target_id,
        "objective": plan.objective, "evidence": plan.evidence or {},
        "changes": plan.proposed_diff or [], "execution_payload": plan.execution_payload or {},
        "risk": plan.risk_assessment, "rollback_plan": plan.rollback_plan or {},
        "status": plan.status, "approval_required_text": "CONFIRMO EXECUTAR",
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        "executed_at": plan.executed_at.isoformat() if plan.executed_at else None,
        "execution_result": plan.execution_result,
    }


class ActionService:
    async def create_dry_run(self, db: AsyncSession, user_id: UUID, *, profile_id: UUID,
                             objective: str, evidence: dict, changes: list[dict], risk: str,
                             session_id: UUID | None = None):
        profile = (await db.execute(select(Profile).where(
            Profile.id == profile_id, Profile.user_id == user_id
        ))).scalar_one_or_none()
        if not profile:
            raise LookupError("Profile não encontrado")
        candidate_config, normalized = apply_changes(profile.config or {}, changes)
        plan = CopilotActionPlan(
            user_id=user_id, session_id=session_id, action_type="UPDATE_PROFILE_CONFIG",
            target_type="PROFILE", target_id=str(profile.id), objective=objective,
            evidence=evidence, proposed_diff=normalized,
            execution_payload={
                "mode": "CREATE_SHADOW_CANDIDATE", "source_profile_id": str(profile.id),
                "candidate_config": candidate_config,
                "source_updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            },
            risk_assessment=risk,
            rollback_plan={"action": "DEACTIVATE_CANDIDATE", "active_profile_unchanged": True},
            target_state_hash=profile_state_hash(profile), status="DRY_RUN",
        )
        db.add(plan)
        await db.flush()
        db.add(CopilotAuditLog(
            user_id=user_id, session_id=session_id, event_type="ACTION_DRY_RUN_CREATED",
            actor_user_id=user_id, action_plan_id=plan.id,
            payload={"profile_id": str(profile.id), "objective": objective, "diff": normalized,
                     "state_hash": plan.target_state_hash},
        ))
        await db.commit()
        await db.refresh(plan)
        return action_to_dict(plan)

    async def approve(self, db: AsyncSession, user_id: UUID, plan_id: UUID, confirmation_text: str):
        confirmation = " ".join(confirmation_text.strip().upper().split())
        if confirmation not in APPROVAL_TEXTS:
            raise ValueError("Confirmação inválida. Digite exatamente CONFIRMO EXECUTAR")
        plan = await self._get(db, user_id, plan_id, lock=True)
        if plan.status != "DRY_RUN":
            raise ValueError(f"Plano não pode ser aprovado no status {plan.status}")
        now = datetime.now(timezone.utc)
        plan.status = "APPROVED"
        plan.approved_at = now
        plan.approved_by = user_id
        plan.approval_text = confirmation
        db.add(CopilotAuditLog(
            user_id=user_id, session_id=plan.session_id, event_type="ACTION_APPROVED",
            actor_user_id=user_id, action_plan_id=plan.id,
            payload={"confirmation_text": confirmation, "approved_at": now.isoformat()},
        ))
        await db.commit()
        await db.refresh(plan)
        return action_to_dict(plan)

    async def execute(self, db: AsyncSession, user_id: UUID, plan_id: UUID):
        try:
            plan = await self._get(db, user_id, plan_id, lock=True)
            if plan.status != "APPROVED" or plan.approved_by != user_id:
                raise ValueError("Plano não está aprovado pelo usuário atual")
            if plan.action_type != "UPDATE_PROFILE_CONFIG":
                raise ValueError("Tipo de ação não suportado")
            source_id = UUID(plan.target_id)
            source = (await db.execute(select(Profile).where(
                Profile.id == source_id, Profile.user_id == user_id
            ).with_for_update())).scalar_one_or_none()
            if not source:
                raise LookupError("Profile de origem não encontrado")
            current_hash = profile_state_hash(source)
            if current_hash != plan.target_state_hash:
                plan.status = "STALE"
                db.add(CopilotAuditLog(
                    user_id=user_id, session_id=plan.session_id, event_type="ACTION_BLOCKED_STALE_STATE",
                    actor_user_id=user_id, action_plan_id=plan.id,
                    payload={"expected_hash": plan.target_state_hash, "current_hash": current_hash},
                ))
                await db.commit()
                raise ValueError("O profile mudou após o DRY_RUN; gere um novo plano")
            now = datetime.now(timezone.utc)
            candidate = Profile(
                user_id=user_id,
                name=f"{source.name} · Co-Pilot {str(plan.id)[:8]}",
                description=f"Candidato shadow gerado pelo Co-Pilot a partir de {source.name}",
                is_active=True,
                config=deepcopy(plan.execution_payload["candidate_config"]),
                profile_role=source.profile_role,
                pipeline_order=source.pipeline_order,
                pipeline_label=source.pipeline_label,
                auto_pilot_enabled=False,
                auto_pilot_config=deepcopy(source.auto_pilot_config or {}),
                profile_type=source.profile_type,
                profile_version=now,
                generated_by="profile_intelligence_copilot",
                is_shadow_only=True,
                live_trading_enabled=False,
                created_at=now,
                updated_at=now,
            )
            db.add(candidate)
            await db.flush()
            db.add(ProfileAuditLog(
                user_id=user_id, profile_id=candidate.id, changed_by=user_id,
                change_source="profile_intelligence_copilot",
                change_description=f"Action plan {plan.id}: {plan.objective}",
                previous_config=deepcopy(source.config or {}), new_config=deepcopy(candidate.config),
                previous_profile_version=source.profile_version, new_profile_version=now,
            ))
            result = {
                "status": "EXECUTED", "candidate_profile_id": str(candidate.id),
                "source_profile_id": str(source.id), "candidate_state": "SHADOW_ONLY",
                "live_profile_changed": False, "shadow_validation_required": True,
            }
            plan.status = "EXECUTED"
            plan.executed_at = now
            plan.execution_result = result
            db.add(CopilotAuditLog(
                user_id=user_id, session_id=plan.session_id, event_type="ACTION_EXECUTED",
                actor_user_id=user_id, action_plan_id=plan.id,
                payload={**result, "diff": plan.proposed_diff, "rollback": plan.rollback_plan},
            ))
            await db.commit()
            await db.refresh(plan)
            return action_to_dict(plan)
        except Exception:
            if db.in_transaction():
                await db.rollback()
            raise

    async def _get(self, db: AsyncSession, user_id: UUID, plan_id: UUID, lock: bool = False):
        query = select(CopilotActionPlan).where(
            CopilotActionPlan.id == plan_id, CopilotActionPlan.user_id == user_id
        )
        if lock:
            query = query.with_for_update()
        plan = (await db.execute(query)).scalar_one_or_none()
        if not plan:
            raise LookupError("Action plan não encontrado")
        return plan


action_service = ActionService()
