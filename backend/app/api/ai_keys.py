"""
AI Provider Keys API — /api/ai-keys
Stores and manages encrypted AI provider API keys per user.
Keys are NEVER returned in plain text — only key_hint is exposed.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, Literal
from uuid import UUID

import jwt as pyjwt

from ..config import settings
from ..database import get_db

security = HTTPBearer()

PROVIDERS = ("anthropic", "openai", "gemini")

PROVIDER_META = {
    "anthropic": {
        "name": "Anthropic",
        "prefix": "sk-ant-",
        "docs_url": "https://console.anthropic.com/settings/keys",
    },
    "openai": {
        "name": "OpenAI",
        "prefix": "sk-",
        "docs_url": "https://platform.openai.com/api-keys",
    },
    "gemini": {
        "name": "Gemini",
        "prefix": "AIza",
        "docs_url": "https://aistudio.google.com/apikey",
    },
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


router = APIRouter(prefix="/api/ai-keys", tags=["AI Provider Keys"])


class SaveKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=20)
    api_secret: Optional[str] = None
    label: Optional[str] = Field(None, max_length=100)
    monthly_token_limit: Optional[int] = Field(None, ge=1_000)


@router.get("")
async def list_keys(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    from ..services.ai_keys_service import get_ai_key_info

    result = []
    for p in PROVIDERS:
        meta = PROVIDER_META[p]
        info = await get_ai_key_info(db, user_id, p)
        row = {
            **meta,
            "provider": p,
            "is_configured": info is not None,
            "is_validated": False,
            "test_status": None,
            "key_hint": None,
            "label": None,
            "test_error": None,
            "last_tested_at": None,
            "tokens_used_month": None,
            "monthly_token_limit": None,
        }
        if info:
            row.update(info)
            row["is_configured"] = True
        result.append(row)
    return result


@router.post("/{provider}")
async def save_key(
    provider: str,
    body: SaveKeyRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    if provider not in PROVIDER_META:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    from ..services.ai_keys_service import save_ai_key

    prefix = PROVIDER_META[provider]["prefix"]
    if not body.api_key.startswith(prefix):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {provider} key. Must start with \"{prefix}\".",
        )

    info = await save_ai_key(
        db, user_id, provider,
        body.api_key, body.api_secret,
        body.label, body.monthly_token_limit,
    )
    return {"status": "saved", "provider": provider, "key_hint": info["key_hint"]}


@router.delete("/{provider}")
async def delete_key(
    provider: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    if provider not in PROVIDER_META:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    from ..services.ai_keys_service import delete_ai_key

    deleted = await delete_ai_key(db, user_id, provider)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No active {provider} key found.")
    return {"status": "deleted", "provider": provider}


@router.post("/{provider}/test")
async def test_key(
    provider: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    if provider not in PROVIDER_META:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    if provider != "anthropic":
        raise HTTPException(status_code=501, detail=f"Test not implemented for {provider}.")

    from ..services.ai_keys_service import test_anthropic_key

    success, message = await test_anthropic_key(db, user_id, provider)
    return {"provider": provider, "success": success, "message": message}
