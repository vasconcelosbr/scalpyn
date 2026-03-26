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

PROVIDERS = ("anthropic", "openai", "gemini", "coinmarketcap")

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
    "coinmarketcap": {
        "name": "CoinMarketCap",
        "prefix": "",
        "docs_url": "https://pro.coinmarketcap.com/account",
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


_MAX_TOKEN_LIMIT = 100_000_000  # 100M tokens


class SaveKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=20)
    api_secret: Optional[str] = None
    label: Optional[str] = Field(None, max_length=100)
    monthly_token_limit: Optional[int] = Field(None, ge=1_000, le=_MAX_TOKEN_LIMIT)


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
    if prefix and not body.api_key.startswith(prefix):
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

    if provider == "coinmarketcap":
        from ..services.ai_keys_service import get_decrypted_api_key
        import httpx
        plain_key = await get_decrypted_api_key(db, user_id, provider)
        if not plain_key:
            raise HTTPException(status_code=404, detail="CoinMarketCap key not configured.")
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://pro-api.coinmarketcap.com/v1/key/info",
                    headers={"X-CMC_PRO_API_KEY": plain_key, "Accept": "application/json"},
                )
            if resp.status_code == 200:
                data = resp.json()
                plan = data.get("data", {}).get("plan", {}).get("name", "")
                credits = data.get("data", {}).get("usage", {}).get("current_month", {}).get("credits_used", "?")
                return {
                    "provider": provider,
                    "success": True,
                    "message": f"Conectado. Plano: {plan}. Créditos usados este mês: {credits}.",
                }
            else:
                return {"provider": provider, "success": False, "message": f"Chave inválida (HTTP {resp.status_code})."}
        except Exception as e:
            return {"provider": provider, "success": False, "message": f"Erro ao conectar: {str(e)}"}

    if provider == "gemini":
        from ..services.ai_keys_service import get_decrypted_api_key
        import httpx
        plain_key = await get_decrypted_api_key(db, user_id, provider)
        if not plain_key:
            raise HTTPException(status_code=404, detail="Chave Gemini não configurada.")
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": plain_key},
                    headers={"Accept": "application/json"},
                )
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                names = [m.get("displayName", m.get("name", "")) for m in models[:3]]
                return {
                    "provider": provider,
                    "success": True,
                    "message": f"Conectado. {len(models)} modelos disponíveis: {', '.join(names)}.",
                }
            elif resp.status_code == 400:
                return {"provider": provider, "success": False, "message": "Chave inválida (formato incorreto)."}
            elif resp.status_code == 403:
                return {"provider": provider, "success": False, "message": "Chave inválida ou sem permissão. Verifique no Google AI Studio."}
            else:
                return {"provider": provider, "success": False, "message": f"Erro HTTP {resp.status_code} ao validar chave."}
        except Exception as e:
            return {"provider": provider, "success": False, "message": f"Erro de conexão: {str(e)}"}

    if provider == "openai":
        from ..services.ai_keys_service import get_decrypted_api_key
        import httpx
        plain_key = await get_decrypted_api_key(db, user_id, provider)
        if not plain_key:
            raise HTTPException(status_code=404, detail="Chave OpenAI não configurada.")
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {plain_key}", "Accept": "application/json"},
                )
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                return {
                    "provider": provider,
                    "success": True,
                    "message": f"Conectado. {len(models)} modelos disponíveis.",
                }
            elif resp.status_code == 401:
                return {"provider": provider, "success": False, "message": "Chave inválida ou expirada."}
            else:
                return {"provider": provider, "success": False, "message": f"Erro HTTP {resp.status_code}."}
        except Exception as e:
            return {"provider": provider, "success": False, "message": f"Erro de conexão: {str(e)}"}

    if provider not in ("anthropic",):
        raise HTTPException(status_code=404, detail=f"Provider não suportado: {provider}.")

    from ..services.ai_keys_service import test_anthropic_key

    success, message = await test_anthropic_key(db, user_id, provider)
    if not message:
        message = "Erro ao conectar. Verifique os logs do servidor."
    return {"provider": provider, "success": success, "message": message}
