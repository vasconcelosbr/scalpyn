"""
AI Skills API — /api/ai-skills
CRUD para gerenciamento de prompts de sistema (Skills de IA) por usuário.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
import uuid as uuid_lib
from datetime import datetime, timezone

import jwt as pyjwt

from ..config import settings
from ..database import get_db
from ..models.ai_skill import AiSkill
from ..services.preset_ia_service import ROLE_PROMPTS

security = HTTPBearer()

ROLE_LABELS = {
    "universe_filter": "Pool — Filtro de Universo (Stage 0)",
    "primary_filter":  "L1 — Filtro Primário (Stage 1)",
    "score_engine":    "L2 — Score Engine (Stage 2)",
    "acquisition_queue": "L3 — Fila de Aquisição (Stage 3)",
}


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UUID:
    token = credentials.credentials
    try:
        payload = pyjwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return UUID(payload["sub"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


router = APIRouter(prefix="/api/ai-skills", tags=["AI Skills"])


class CreateSkillRequest(BaseModel):
    name:        str  = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    role_key:    Optional[str] = Field(None, max_length=60)
    prompt_text: str  = Field(..., min_length=10)
    is_active:   bool = True


class UpdateSkillRequest(BaseModel):
    name:        Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    role_key:    Optional[str] = Field(None, max_length=60)
    prompt_text: Optional[str] = Field(None, min_length=10)
    is_active:   Optional[bool] = None


def _skill_to_dict(skill: AiSkill) -> dict:
    return {
        "id":          str(skill.id),
        "name":        skill.name,
        "description": skill.description,
        "role_key":    skill.role_key,
        "role_label":  ROLE_LABELS.get(skill.role_key, skill.role_key) if skill.role_key else None,
        "prompt_text": skill.prompt_text,
        "is_active":   skill.is_active,
        "created_at":  skill.created_at.isoformat() if skill.created_at else None,
        "updated_at":  skill.updated_at.isoformat() if skill.updated_at else None,
    }


@router.get("/defaults")
async def get_defaults(
    user_id: UUID = Depends(get_current_user_id),
):
    """Retorna os prompts hardcoded do ROLE_PROMPTS como referência."""
    return [
        {
            "role_key":    key,
            "role_label":  ROLE_LABELS.get(key, key),
            "prompt_text": prompt,
        }
        for key, prompt in ROLE_PROMPTS.items()
    ]


@router.get("")
async def list_skills(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    result = await db.execute(
        select(AiSkill)
        .where(AiSkill.user_id == user_id)
        .order_by(AiSkill.created_at.asc())
    )
    skills = result.scalars().all()
    return [_skill_to_dict(s) for s in skills]


@router.post("", status_code=201)
async def create_skill(
    body: CreateSkillRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    existing = await db.execute(
        select(AiSkill).where(
            and_(AiSkill.user_id == user_id, AiSkill.name == body.name)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Já existe uma Skill com o nome '{body.name}'."
        )

    skill = AiSkill(
        id          = uuid_lib.uuid4(),
        user_id     = user_id,
        name        = body.name,
        description = body.description,
        role_key    = body.role_key or None,
        prompt_text = body.prompt_text,
        is_active   = body.is_active,
        created_at  = datetime.now(timezone.utc),
        updated_at  = datetime.now(timezone.utc),
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return _skill_to_dict(skill)


@router.put("/{skill_id}")
async def update_skill(
    skill_id: UUID,
    body: UpdateSkillRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    result = await db.execute(
        select(AiSkill).where(
            and_(AiSkill.id == skill_id, AiSkill.user_id == user_id)
        )
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill não encontrada.")

    if body.name is not None:
        conflict = await db.execute(
            select(AiSkill).where(
                and_(
                    AiSkill.user_id == user_id,
                    AiSkill.name == body.name,
                    AiSkill.id != skill_id,
                )
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Já existe uma Skill com o nome '{body.name}'."
            )
        skill.name = body.name

    if body.description is not None:
        skill.description = body.description
    if body.role_key is not None:
        skill.role_key = body.role_key if body.role_key else None
    if body.prompt_text is not None:
        skill.prompt_text = body.prompt_text
    if body.is_active is not None:
        skill.is_active = body.is_active

    skill.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(skill)
    return _skill_to_dict(skill)


@router.delete("/{skill_id}", status_code=200)
async def delete_skill(
    skill_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    result = await db.execute(
        select(AiSkill).where(
            and_(AiSkill.id == skill_id, AiSkill.user_id == user_id)
        )
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill não encontrada.")

    await db.delete(skill)
    await db.commit()
    return {"status": "deleted", "id": str(skill_id)}
