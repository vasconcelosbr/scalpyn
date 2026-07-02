import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.copilot import CopilotAuditLog, CopilotSkill


CRITICAL_SKILL_TYPES = {"RISK_RULE", "TRADING_DECISION", "OPERATIONAL_RULE", "PARAMETER_CHANGE", "PIPELINE_CHANGE"}


def requires_approval(skill_type: str) -> bool:
    return skill_type.upper() in CRITICAL_SKILL_TYPES


def skill_to_dict(skill: CopilotSkill) -> dict:
    return {
        "id": str(skill.id), "name": skill.name, "skill_type": skill.skill_type,
        "content": skill.content, "metadata": skill.skill_metadata or {},
        "version": skill.version, "status": skill.status,
        "confidence": float(skill.confidence) if skill.confidence is not None else None,
        "source": skill.source, "requires_approval": skill.requires_approval,
        "approved_by": str(skill.approved_by) if skill.approved_by else None,
        "approved_at": skill.approved_at.isoformat() if skill.approved_at else None,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


class SkillService:
    async def list(self, db: AsyncSession, user_id: UUID, status: str | None = None):
        query = select(CopilotSkill).where(CopilotSkill.user_id == user_id)
        if status:
            query = query.where(CopilotSkill.status == status)
        rows = (await db.execute(query.order_by(CopilotSkill.name, CopilotSkill.version.desc()))).scalars().all()
        return [skill_to_dict(row) for row in rows]

    async def retrieve(self, db: AsyncSession, user_id: UUID, query_text: str, limit: int = 8):
        rows = (await db.execute(
            select(CopilotSkill).where(CopilotSkill.user_id == user_id, CopilotSkill.status == "ACTIVE")
        )).scalars().all()
        terms = {term for term in re.findall(r"[a-zA-ZÀ-ÿ0-9_]+", query_text.lower()) if len(term) >= 4}
        ranked = sorted(
            rows,
            key=lambda skill: sum(term in f"{skill.name} {skill.skill_type} {skill.content}".lower() for term in terms),
            reverse=True,
        )
        return [skill_to_dict(row) for row in ranked[:limit]]

    async def create(self, db: AsyncSession, user_id: UUID, *, name: str, skill_type: str,
                     content: str, metadata: dict, confidence: float | None, source: str | None,
                     actor_user_id: UUID | None = None):
        version = (await db.scalar(
            select(func.coalesce(func.max(CopilotSkill.version), 0)).where(
                CopilotSkill.user_id == user_id, CopilotSkill.name == name
            )
        )) + 1
        critical = requires_approval(skill_type)
        skill = CopilotSkill(
            user_id=user_id, name=name, skill_type=skill_type.upper(), content=content,
            skill_metadata=metadata, version=version,
            status="PENDING_APPROVAL" if critical else "ACTIVE",
            confidence=confidence, source=source, requires_approval=critical,
            approved_by=None if critical else actor_user_id,
            approved_at=None if critical else datetime.now(timezone.utc),
        )
        db.add(skill)
        db.add(CopilotAuditLog(
            user_id=user_id, event_type="SKILL_CANDIDATE_CREATED" if critical else "SKILL_CREATED",
            actor_user_id=actor_user_id, payload={"name": name, "version": version, "skill_type": skill_type.upper()},
        ))
        await db.commit()
        await db.refresh(skill)
        return skill_to_dict(skill)

    async def approve(self, db: AsyncSession, user_id: UUID, skill_id: UUID):
        skill = await self._get(db, user_id, skill_id)
        if skill.status != "PENDING_APPROVAL":
            raise ValueError("A skill não está pendente de aprovação")
        skill.status = "ACTIVE"
        skill.approved_by = user_id
        skill.approved_at = datetime.now(timezone.utc)
        skill.updated_at = datetime.now(timezone.utc)
        db.add(CopilotAuditLog(user_id=user_id, event_type="SKILL_APPROVED", actor_user_id=user_id,
                               payload={"skill_id": str(skill.id), "version": skill.version}))
        await db.commit()
        await db.refresh(skill)
        return skill_to_dict(skill)

    async def _get(self, db: AsyncSession, user_id: UUID, skill_id: UUID):
        skill = (await db.execute(select(CopilotSkill).where(
            CopilotSkill.id == skill_id, CopilotSkill.user_id == user_id
        ))).scalar_one_or_none()
        if not skill:
            raise LookupError("Skill não encontrada")
        return skill


skill_service = SkillService()
