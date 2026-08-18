"""
ai_keys_service.py
------------------
Secure management of AI provider API keys.
Encryption: AES-256 via cryptography.fernet
The key is NEVER returned in plain text by the API — only key_hint.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Encryption helpers ────────────────────────────────────────────────────────

def validate_encryption_key_configuration(environment: str | None = None) -> str | None:
    """Fail closed outside development when the encryption key is missing."""
    environment = (
        environment
        or os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("RAILWAY_ENVIRONMENT_NAME")
        or "development"
    ).lower()
    key = os.getenv("AI_KEYS_ENCRYPTION_KEY")
    if not key and environment not in {"development", "dev", "test", "local"}:
        raise RuntimeError("AI_KEYS_ENCRYPTION_KEY is required outside development")
    return key


def _fernet():
    from cryptography.fernet import Fernet
    key = validate_encryption_key_configuration()
    if not key:
        logger.warning("[AIKeys] AI_KEYS_ENCRYPTION_KEY not set — generating ephemeral key (dev only).")
        key = Fernet.generate_key().decode()
    raw = key.encode() if isinstance(key, str) else key
    return Fernet(raw)


def encrypt_value(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode())


def decrypt_value(enc: bytes) -> str:
    try:
        return _fernet().decrypt(enc).decode()
    except Exception as e:
        raise ValueError(
            "Falha ao descriptografar a chave. "
            "Verifique se AI_KEYS_ENCRYPTION_KEY está configurada corretamente no Cloud Run. "
            f"Detalhe: {type(e).__name__}: {e}"
        ) from e


def make_hint(key: str) -> str:
    return f"{key[:10]}...{key[-4:]}" if len(key) >= 12 else "***"


def _safe(r) -> dict:
    return {
        "id":                  str(r.id),
        "provider":            r.provider,
        "key_hint":            r.key_hint,
        "label":               r.label,
        "default_model":       r.default_model,
        "is_active":           r.is_active,
        "is_validated":        r.is_validated,
        "test_status":         r.test_status,
        "test_error":          r.test_error,
        "last_tested_at":      r.last_tested_at.isoformat() if r.last_tested_at else None,
        "last_used_at":        r.last_used_at.isoformat() if r.last_used_at else None,
        "monthly_token_limit": r.monthly_token_limit,
        "tokens_used_month":   r.tokens_used_month,
        "created_at":          r.created_at.isoformat(),
    }


# ── DB helpers (async) ────────────────────────────────────────────────────────

async def _get_record(db: AsyncSession, user_id: UUID, provider: str):
    from ..models.ai_provider_key import AIProviderKey
    result = await db.execute(
        select(AIProviderKey).where(
            AIProviderKey.user_id == user_id,
            AIProviderKey.provider == provider,
            AIProviderKey.is_active == True,
        )
    )
    return result.scalars().first()


_MAX_TOKEN_LIMIT = 100_000_000  # 100M tokens


async def save_ai_key(
    db: AsyncSession,
    user_id: UUID,
    provider: str,
    api_key: str,
    api_secret: Optional[str] = None,
    label: Optional[str] = None,
    monthly_token_limit: Optional[int] = None,
    default_model: Optional[str] = None,
) -> dict:
    from ..models.ai_provider_key import AIProviderKey

    # Deactivate any existing active key for this provider
    existing = await _get_record(db, user_id, provider)
    if existing:
        existing.is_active = False
        await db.flush()

    rec = AIProviderKey(
        user_id=user_id,
        provider=provider,
        api_key_encrypted=encrypt_value(api_key),
        api_secret_encrypted=encrypt_value(api_secret) if api_secret else None,
        key_hint=make_hint(api_key),
        label=label or provider.capitalize(),
        default_model=default_model,
        is_active=True,
        is_validated=False,
        test_status="pending",
        monthly_token_limit=min(monthly_token_limit, _MAX_TOKEN_LIMIT) if monthly_token_limit else None,
        tokens_used_month=0,
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return _safe(rec)


async def get_ai_key_info(db: AsyncSession, user_id: UUID, provider: str) -> Optional[dict]:
    """
    Retorna info da chave armazenada sem expor o valor.
    Captura exceções silenciosamente para não quebrar o endpoint /api/ai-keys
    caso a tabela não exista ou AI_KEYS_ENCRYPTION_KEY esteja errada.
    """
    try:
        r = await _get_record(db, user_id, provider)
        return _safe(r) if r else None
    except Exception as exc:
        logger.error("[AIKeys] get_ai_key_info(%s) DB error: %s", provider, exc, exc_info=True)
        return None


async def get_decrypted_api_key(db: AsyncSession, user_id: UUID, provider: str) -> Optional[str]:
    r = await _get_record(db, user_id, provider)
    if not r:
        return None
    try:
        return decrypt_value(r.api_key_encrypted)
    except Exception:
        return None


async def get_anthropic_api_key(db: AsyncSession, user_id: UUID) -> str:
    """Resolve the tenant key with the existing system-key fallback."""
    user_key = await get_decrypted_api_key(db, user_id, "anthropic")
    if user_key:
        return user_key

    system_key = os.getenv("ANTHROPIC_API_KEY")
    if system_key:
        return system_key

    raise ValueError(
        f"No Anthropic key configured for user={user_id}. "
        "Set up a key at /settings/general → AI Integrations."
    )


async def delete_ai_key(db: AsyncSession, user_id: UUID, provider: str) -> bool:
    r = await _get_record(db, user_id, provider)
    if not r:
        return False
    r.is_active = False
    await db.commit()
    return True


async def get_anthropic_client(db: AsyncSession, user_id: UUID):
    """
    Returns an Anthropic client using the user's stored key.
    Falls back to ANTHROPIC_API_KEY env var if no user key is configured.
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic package not installed. Run: pip install anthropic")

    api_key = await get_anthropic_api_key(db, user_id)
    return anthropic.Anthropic(api_key=api_key)


async def _save_test_result(db: AsyncSession, r, success: bool, msg: str) -> None:
    r.is_validated = success
    r.test_status = "ok" if success else "error"
    r.test_error = None if success else msg
    r.last_tested_at = datetime.now(timezone.utc)
    await db.commit()


async def mark_key_test_result(db: AsyncSession, user_id: UUID, provider: str, success: bool, msg: str) -> None:
    """Persist is_validated/test_status/last_tested_at after a provider connection test.
    Providers whose /test handler only returns an ephemeral response (never calling
    this) leave is_validated permanently False, which silently blocks them at the
    VALIDATED_PROVIDER_KEY_AND_BUDGET_REQUIRED gate in systemic_langgraph_bridge.py."""
    r = await _get_record(db, user_id, provider)
    if r is not None:
        await _save_test_result(db, r, success, msg)


async def test_anthropic_key(db: AsyncSession, user_id: UUID, provider: str = "anthropic") -> Tuple[bool, str]:
    r = await _get_record(db, user_id, provider)
    if not r:
        return False, "Nenhuma chave configurada para este provider."

    try:
        api_key = decrypt_value(r.api_key_encrypted)
    except ValueError as e:
        msg = str(e)
        logger.error(f"[AIKeys] Decrypt failed user={user_id}: {msg}")
        await _save_test_result(db, r, False, msg)
        return False, msg

    # Key validation must not consume generation tokens. The authenticated
    # catalog proves that the provider accepts the credential and supplies the
    # exact model IDs used by the separately approved canary.
    from ..ai_orchestration.provider_adapters import AnthropicCatalogAdapter, ProviderCatalogError

    msg = ""
    try:
        await AnthropicCatalogAdapter().list_model_ids(api_key=api_key)
        await _save_test_result(db, r, True, "")
        logger.info("[AIKeys] Anthropic catalog validation OK user=%s", user_id)
        return True, "Conexão autenticada e catálogo consultado sem geração."
    except ProviderCatalogError as exc:
        if exc.status_code == 401:
            msg = "Chave inválida ou expirada."
        elif exc.status_code == 403:
            msg = "Permissão negada. Verifique os escopos da chave."
        elif exc.code == "PROVIDER_CATALOG_TIMEOUT":
            msg = "Timeout ao consultar o catálogo do provider."
        elif exc.code == "PROVIDER_CATALOG_CONNECTION_ERROR":
            msg = "Erro de conexão ao consultar o catálogo do provider."
        elif exc.code in {"PROVIDER_CATALOG_RESPONSE_INVALID", "PROVIDER_CATALOG_EMPTY"}:
            msg = "Resposta inválida do catálogo do provider."
        else:
            msg = f"Erro HTTP {exc.status_code} ao consultar o catálogo do provider."

    if not msg:
        msg = "Erro desconhecido. Verifique os logs do servidor."

    await _save_test_result(db, r, False, msg)
    return False, msg
