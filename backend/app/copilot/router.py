from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.config import get_current_user_id
from ..database import get_db
from ..models.copilot import CopilotMessage, CopilotSession
from .action_service import action_service, action_to_dict
from .agent import copilot_agent
from .pattern_service import PatternService
from .query_executor import QueryExecutor, SqlGuardError
from .schema_analyzer import SchemaAnalyzer
from .schemas import (ApprovalRequest, ChatRequest, DryRunRequest, PatternRequest,
                      QueryRequest, SkillCreateRequest, SkillUpdateRequest)
from .skill_service import skill_service, skill_to_dict


router = APIRouter(prefix="/api/copilot", tags=["profile-intelligence-copilot"])
query_executor = QueryExecutor()
schema_analyzer = SchemaAnalyzer(query_executor)
pattern_service = PatternService(query_executor)


def _http_error(exc: Exception):
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (ValueError, SqlGuardError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


async def _session(db: AsyncSession, user_id: UUID, session_id: UUID | None, context: dict | None = None):
    if session_id:
        row = (await db.execute(select(CopilotSession).where(
            CopilotSession.id == session_id, CopilotSession.user_id == user_id,
            CopilotSession.status == "ACTIVE",
        ))).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Sessão não encontrada")
        if context:
            row.context = context
        return row
    row = CopilotSession(user_id=user_id, context=context or {}, status="ACTIVE")
    db.add(row)
    await db.flush()
    return row


@router.post("/chat")
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db),
               user_id: UUID = Depends(get_current_user_id)):
    session = await _session(db, user_id, body.session_id, body.context.model_dump())
    db.add(CopilotMessage(session_id=session.id, role="user", content=body.message,
                          message_metadata={"context": body.context.model_dump()}))
    await db.commit()
    try:
        result = await copilot_agent.run(
            db, user_id, body.message, session_id=session.id,
            context=body.context.model_dump(), provider=body.provider, model=body.model,
        )
        db.add(CopilotMessage(
            session_id=session.id, role="assistant", content=result["answer"],
            message_metadata={"query_ids": [q.get("id") for q in result["queries"]],
                              "action_plan_id": (result["action_plan"] or {}).get("id"),
                              "skills_used": [s["id"] for s in result["skills_used"]]},
        ))
        await db.commit()
        return {"session_id": str(session.id), **result}
    except Exception as exc:
        db.add(CopilotMessage(session_id=session.id, role="system", content=str(exc),
                              message_metadata={"error": type(exc).__name__}))
        await db.commit()
        raise _http_error(exc)

@router.post("/query")
async def execute_query(body: QueryRequest, db: AsyncSession = Depends(get_db),
                        user_id: UUID = Depends(get_current_user_id)):
    try:
        return await query_executor.execute(db, user_id, body.sql, body.params,
                                            reason=body.reason, session_id=body.session_id)
    except Exception as exc:
        raise _http_error(exc)


@router.get("/schema-map")
async def schema_map(session_id: UUID | None = None, db: AsyncSession = Depends(get_db),
                     user_id: UUID = Depends(get_current_user_id)):
    try:
        return await schema_analyzer.analyze(db, user_id, session_id)
    except Exception as exc:
        raise _http_error(exc)


@router.post("/patterns")
async def patterns(body: PatternRequest, db: AsyncSession = Depends(get_db),
                   user_id: UUID = Depends(get_current_user_id)):
    try:
        return await pattern_service.discover(db, user_id, body.analysis, body.lookback_days,
                                              body.min_sample, body.session_id)
    except Exception as exc:
        raise _http_error(exc)


@router.post("/actions/dry-run", status_code=201)
async def dry_run(body: DryRunRequest, db: AsyncSession = Depends(get_db),
                  user_id: UUID = Depends(get_current_user_id)):
    try:
        return await action_service.create_dry_run(
            db, user_id, profile_id=body.profile_id, objective=body.objective,
            evidence=body.evidence, changes=[item.model_dump() for item in body.changes],
            risk=body.risk, session_id=body.session_id,
        )
    except Exception as exc:
        raise _http_error(exc)


@router.get("/actions/{plan_id}")
async def get_action(plan_id: UUID, db: AsyncSession = Depends(get_db),
                     user_id: UUID = Depends(get_current_user_id)):
    try:
        return action_to_dict(await action_service._get(db, user_id, plan_id))
    except Exception as exc:
        raise _http_error(exc)


@router.post("/actions/{plan_id}/approve")
async def approve_action(plan_id: UUID, body: ApprovalRequest, db: AsyncSession = Depends(get_db),
                         user_id: UUID = Depends(get_current_user_id)):
    try:
        return await action_service.approve(db, user_id, plan_id, body.confirmation_text)
    except Exception as exc:
        raise _http_error(exc)


@router.post("/actions/{plan_id}/execute")
async def execute_action(plan_id: UUID, db: AsyncSession = Depends(get_db),
                         user_id: UUID = Depends(get_current_user_id)):
    try:
        return await action_service.execute(db, user_id, plan_id)
    except Exception as exc:
        raise _http_error(exc)


@router.get("/skills")
async def list_skills(status: str | None = None, db: AsyncSession = Depends(get_db),
                      user_id: UUID = Depends(get_current_user_id)):
    return await skill_service.list(db, user_id, status)


@router.post("/skills", status_code=201)
async def create_skill(body: SkillCreateRequest, db: AsyncSession = Depends(get_db),
                       user_id: UUID = Depends(get_current_user_id)):
    try:
        return await skill_service.create(
            db, user_id, name=body.name, skill_type=body.skill_type, content=body.content,
            metadata=body.metadata, confidence=body.confidence, source=body.source,
            actor_user_id=user_id,
        )
    except Exception as exc:
        raise _http_error(exc)


@router.patch("/skills/{skill_id}")
async def update_skill(skill_id: UUID, body: SkillUpdateRequest, db: AsyncSession = Depends(get_db),
                       user_id: UUID = Depends(get_current_user_id)):
    try:
        skill = await skill_service._get(db, user_id, skill_id)
        if body.content is not None:
            skill.status = "INACTIVE"
            skill.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return await skill_service.create(
                db, user_id, name=skill.name, skill_type=skill.skill_type, content=body.content,
                metadata=body.metadata if body.metadata is not None else (skill.skill_metadata or {}),
                confidence=body.confidence if body.confidence is not None else (
                    float(skill.confidence) if skill.confidence is not None else None),
                source=skill.source, actor_user_id=user_id,
            )
        if body.status == "ACTIVE" and skill.requires_approval and skill.status == "PENDING_APPROVAL":
            raise ValueError("Use o endpoint /approve para ativar uma skill crítica")
        if body.status is not None:
            skill.status = body.status
        if body.metadata is not None:
            skill.skill_metadata = body.metadata
        if body.confidence is not None:
            skill.confidence = body.confidence
        skill.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(skill)
        return skill_to_dict(skill)
    except Exception as exc:
        raise _http_error(exc)


@router.post("/skills/{skill_id}/approve")
async def approve_skill(skill_id: UUID, db: AsyncSession = Depends(get_db),
                        user_id: UUID = Depends(get_current_user_id)):
    try:
        return await skill_service.approve(db, user_id, skill_id)
    except Exception as exc:
        raise _http_error(exc)
