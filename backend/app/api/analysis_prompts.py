from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.systemic_ai import AIAnalysisPromptRecord, AIAnalysisPromptVersionRecord
from ..models.user import User
from .config import get_current_user_id


router = APIRouter(prefix="/api/ai/modules/analysis-prompts", tags=["AI Analysis Prompts"])

MAX_PROMPT_CHARACTERS = 100_000
MAX_PROMPT_FILE_BYTES = 256 * 1024


def normalize_markdown(value: str) -> str:
    return value.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_name(value: str) -> str:
    return " ".join(value.split())


class PromptVersionPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1_000)
    content_markdown: str = Field(min_length=1, max_length=MAX_PROMPT_CHARACTERS)
    source_type: Literal["UPLOAD_MD", "PASTE"]
    source_filename: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_prompt_name(cls, value: str) -> str:
        normalized = normalize_name(value)
        if not normalized:
            raise ValueError("name must contain text")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("content_markdown")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = normalize_markdown(value)
        if not normalized.strip():
            raise ValueError("content_markdown must contain text")
        return normalized

    @model_validator(mode="after")
    def validate_source(self):
        if len(self.content_markdown.encode("utf-8")) > MAX_PROMPT_FILE_BYTES:
            raise ValueError("analysis prompt exceeds 256 KiB")
        if self.source_type == "UPLOAD_MD":
            if not self.source_filename or not self.source_filename.lower().endswith(".md"):
                raise ValueError("UPLOAD_MD requires a .md source_filename")
        elif self.source_filename:
            raise ValueError("PASTE must not include source_filename")
        return self


class PromptStatusPayload(BaseModel):
    status: Literal["ACTIVE", "ARCHIVED"]


async def current_user(db: AsyncSession, user_id: UUID) -> User:
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail={"code": "USER_NOT_ACTIVE"})
    return user


async def require_admin(db: AsyncSession, user_id: UUID) -> User:
    user = await current_user(db, user_id)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "ADMIN_ACCESS_REQUIRED"})
    return user


def version_response(
    version: AIAnalysisPromptVersionRecord,
    author: User | None,
    *,
    include_content: bool,
) -> dict:
    payload = {
        "id": str(version.id),
        "prompt_id": str(version.prompt_id),
        "version_number": version.version_number,
        "name": version.name_snapshot,
        "description": version.description_snapshot,
        "content_hash": version.content_hash,
        "source_type": version.source_type,
        "source_filename": version.source_filename,
        "created_by": str(version.created_by) if version.created_by else None,
        "created_by_name": author.name if author else "Sistema",
        "created_at": version.created_at.isoformat(),
    }
    if include_content:
        payload["content_markdown"] = version.content_markdown
    return payload


async def prompt_response(
    db: AsyncSession,
    prompt: AIAnalysisPromptRecord,
    *,
    include_content: bool,
    include_versions: bool,
) -> dict:
    current_row = await db.execute(
        select(AIAnalysisPromptVersionRecord, User)
        .outerjoin(User, User.id == AIAnalysisPromptVersionRecord.created_by)
        .where(AIAnalysisPromptVersionRecord.id == prompt.current_version_id)
    )
    current = current_row.first()
    if current is None:
        raise HTTPException(status_code=409, detail={"code": "ANALYSIS_PROMPT_CURRENT_VERSION_MISSING"})
    current_version, current_author = current
    payload = {
        "id": str(prompt.id),
        "name": prompt.name,
        "status": prompt.status,
        "current_version": version_response(
            current_version,
            current_author,
            include_content=include_content,
        ),
        "created_at": prompt.created_at.isoformat(),
        "updated_at": prompt.updated_at.isoformat(),
        "archived_at": prompt.archived_at.isoformat() if prompt.archived_at else None,
    }
    if include_versions:
        rows = list((await db.execute(
            select(AIAnalysisPromptVersionRecord, User)
            .outerjoin(User, User.id == AIAnalysisPromptVersionRecord.created_by)
            .where(AIAnalysisPromptVersionRecord.prompt_id == prompt.id)
            .order_by(AIAnalysisPromptVersionRecord.version_number.desc())
        )).all())
        payload["versions"] = [
            version_response(version, author, include_content=include_content)
            for version, author in rows
        ]
    return payload


@router.get("")
async def list_analysis_prompts(
    include_archived: bool = False,
    search: str | None = Query(default=None, max_length=160),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    user = await current_user(db, user_id)
    if include_archived and user.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "ADMIN_ACCESS_REQUIRED"})
    statement = select(AIAnalysisPromptRecord)
    if not include_archived:
        statement = statement.where(AIAnalysisPromptRecord.status == "ACTIVE")
    if search and search.strip():
        term = f"%{search.strip()}%"
        statement = statement.where(or_(
            AIAnalysisPromptRecord.name.ilike(term),
            AIAnalysisPromptRecord.name_key.ilike(term.casefold()),
        ))
    prompts = list((await db.execute(
        statement.order_by(AIAnalysisPromptRecord.name.asc())
    )).scalars())
    return {
        "items": [
            await prompt_response(db, prompt, include_content=False, include_versions=False)
            for prompt in prompts
        ],
        "can_manage": user.role == "admin",
    }


@router.get("/{prompt_id}")
async def get_analysis_prompt(
    prompt_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    user = await current_user(db, user_id)
    prompt = await db.get(AIAnalysisPromptRecord, prompt_id)
    if prompt is None or (prompt.status != "ACTIVE" and user.role != "admin"):
        raise HTTPException(status_code=404, detail={"code": "ANALYSIS_PROMPT_NOT_FOUND"})
    return await prompt_response(db, prompt, include_content=True, include_versions=True)


@router.post("", status_code=201)
async def create_analysis_prompt(
    payload: PromptVersionPayload,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    await require_admin(db, user_id)
    now = datetime.now(timezone.utc)
    prompt = AIAnalysisPromptRecord(
        id=uuid4(),
        name=payload.name,
        name_key=payload.name.casefold(),
        status="ACTIVE",
        created_by=user_id,
        updated_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(prompt)
    try:
        await db.flush()
        version = AIAnalysisPromptVersionRecord(
            id=uuid4(),
            prompt_id=prompt.id,
            version_number=1,
            name_snapshot=payload.name,
            description_snapshot=payload.description,
            content_markdown=payload.content_markdown,
            content_hash=content_hash(payload.content_markdown),
            source_type=payload.source_type,
            source_filename=payload.source_filename,
            created_by=user_id,
            created_at=now,
        )
        db.add(version)
        await db.flush()
        prompt.current_version_id = version.id
        await db.commit()
        await db.refresh(prompt)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "ANALYSIS_PROMPT_NAME_CONFLICT"}) from exc
    return await prompt_response(db, prompt, include_content=True, include_versions=True)


@router.post("/{prompt_id}/versions", status_code=201)
async def create_analysis_prompt_version(
    prompt_id: UUID,
    payload: PromptVersionPayload,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    await require_admin(db, user_id)
    prompt = (await db.execute(
        select(AIAnalysisPromptRecord)
        .where(AIAnalysisPromptRecord.id == prompt_id)
        .with_for_update()
    )).scalar_one_or_none()
    if prompt is None:
        raise HTTPException(status_code=404, detail={"code": "ANALYSIS_PROMPT_NOT_FOUND"})
    if prompt.status != "ACTIVE":
        raise HTTPException(status_code=409, detail={"code": "ANALYSIS_PROMPT_ARCHIVED"})
    current = await db.get(AIAnalysisPromptVersionRecord, prompt.current_version_id)
    if current is None:
        raise HTTPException(status_code=409, detail={"code": "ANALYSIS_PROMPT_CURRENT_VERSION_MISSING"})
    next_hash = content_hash(payload.content_markdown)
    if (
        current.name_snapshot == payload.name
        and current.description_snapshot == payload.description
        and current.content_hash == next_hash
        and current.source_type == payload.source_type
        and current.source_filename == payload.source_filename
    ):
        raise HTTPException(status_code=409, detail={"code": "ANALYSIS_PROMPT_VERSION_UNCHANGED"})
    name_conflict = (await db.execute(
        select(AIAnalysisPromptRecord.id).where(
            AIAnalysisPromptRecord.name_key == payload.name.casefold(),
            AIAnalysisPromptRecord.id != prompt.id,
        )
    )).scalar_one_or_none()
    if name_conflict:
        raise HTTPException(status_code=409, detail={"code": "ANALYSIS_PROMPT_NAME_CONFLICT"})
    now = datetime.now(timezone.utc)
    version = AIAnalysisPromptVersionRecord(
        id=uuid4(),
        prompt_id=prompt.id,
        version_number=current.version_number + 1,
        name_snapshot=payload.name,
        description_snapshot=payload.description,
        content_markdown=payload.content_markdown,
        content_hash=next_hash,
        source_type=payload.source_type,
        source_filename=payload.source_filename,
        created_by=user_id,
        created_at=now,
    )
    db.add(version)
    prompt.name = payload.name
    prompt.name_key = payload.name.casefold()
    prompt.current_version_id = version.id
    prompt.updated_by = user_id
    prompt.updated_at = now
    try:
        await db.commit()
        await db.refresh(prompt)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "ANALYSIS_PROMPT_VERSION_CONFLICT"}) from exc
    return await prompt_response(db, prompt, include_content=True, include_versions=True)


@router.patch("/{prompt_id}/status")
async def change_analysis_prompt_status(
    prompt_id: UUID,
    payload: PromptStatusPayload,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    await require_admin(db, user_id)
    prompt = await db.get(AIAnalysisPromptRecord, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail={"code": "ANALYSIS_PROMPT_NOT_FOUND"})
    now = datetime.now(timezone.utc)
    prompt.status = payload.status
    prompt.updated_by = user_id
    prompt.updated_at = now
    prompt.archived_by = user_id if payload.status == "ARCHIVED" else None
    prompt.archived_at = now if payload.status == "ARCHIVED" else None
    await db.commit()
    await db.refresh(prompt)
    return await prompt_response(db, prompt, include_content=True, include_versions=True)
