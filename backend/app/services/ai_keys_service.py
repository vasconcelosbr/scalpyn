"""
ai_keys_service.py
------------------
Secure management of AI provider API keys.
Encryption: AES-256 via cryptography.fernet
The key is NEVER returned in plain text by the API — only key_hint.
"""

import os
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Encryption helpers ────────────────────────────────────────────────────────

def _fernet():
    from cryptography.fernet import Fernet
    key = os.getenv("AI_KEYS_ENCRYPTION_KEY")
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
    r = await _get_record(db, user_id, provider)
    return _safe(r) if r else None


async def get_decrypted_api_key(db: AsyncSession, user_id: UUID, provider: str) -> Optional[str]:
    r = await _get_record(db, user_id, provider)
    if not r:
        return None
    try:
        return decrypt_value(r.api_key_encrypted)
    except Exception:
        return None


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

    user_key = await get_decrypted_api_key(db, user_id, "anthropic")
    if user_key:
        return anthropic.Anthropic(api_key=user_key)

    system_key = os.getenv("ANTHROPIC_API_KEY")
    if system_key:
        return anthropic.Anthropic(api_key=system_key)

    raise ValueError(
        f"No Anthropic key configured for user={user_id}. "
        "Set up a key at /settings/general → AI Integrations."
    )


async def _save_test_result(db: AsyncSession, r, success: bool, msg: str) -> None:
    r.is_validated = success
    r.test_status = "ok" if success else "error"
    r.test_error = None if success else msg
    r.last_tested_at = datetime.now(timezone.utc)
    await db.commit()


async def test_anthropic_key(db: AsyncSession, user_id: UUID, provider: str = "anthropic") -> Tuple[bool, str]:
    r = await _get_record(db, user_id, provider)
    if not r:
        return False, "Nenhuma chave configurada para este provider."

    try:
        import anthropic
    except ImportError:
        msg = "Pacote 'anthropic' não instalado. Execute: pip install anthropic"
        logger.error(f"[AIKeys] {msg}")
        return False, msg

    try:
        api_key = decrypt_value(r.api_key_encrypted)
    except ValueError as e:
        msg = str(e)
        logger.error(f"[AIKeys] Decrypt failed user={user_id}: {msg}")
        await _save_test_result(db, r, False, msg)
        return False, msg

    msg = ""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "Hi"}],
        )
        await _save_test_result(db, r, True, "")
        logger.info(f"[AIKeys] Anthropic test OK user={user_id}")
        return True, "Conexão com a API Anthropic estabelecida com sucesso."

    except anthropic.AuthenticationError as e:
        msg = f"Chave inválida. Verifique se copiou a API key corretamente. (AuthenticationError: {e.message})"
        logger.error(f"[AIKeys] AuthenticationError user={user_id}: {e.message}")

    except anthropic.PermissionDeniedError as e:
        msg = f"Permissão negada. Verifique os escopos da chave. (PermissionDeniedError: {e.message})"
        logger.error(f"[AIKeys] PermissionDeniedError user={user_id}: {e.message}")

    except anthropic.RateLimitError as e:
        msg = f"Rate limit atingido. Aguarde alguns instantes e tente novamente. (RateLimitError: {e.message})"
        logger.warning(f"[AIKeys] RateLimitError user={user_id}: {e.message}")

    except anthropic.APIConnectionError as e:
        msg = f"Erro de conexão com a API Anthropic. Verifique sua rede. (APIConnectionError: {e})"
        logger.error(f"[AIKeys] APIConnectionError user={user_id}: {e}")

    except anthropic.APIStatusError as e:
        msg = f"Erro da API Anthropic (HTTP {e.status_code}): {e.message}"
        logger.error(f"[AIKeys] APIStatusError {e.status_code} user={user_id}: {e.message}")

    except Exception as e:
        tb = traceback.format_exc()
        msg = f"{type(e).__name__}: {str(e) or 'sem mensagem'}"
        logger.error(f"[AIKeys] Erro inesperado user={user_id}:\n{tb}")

    if not msg:
        msg = "Erro desconhecido. Verifique os logs do Cloud Run."

    await _save_test_result(db, r, False, msg)
    return False, msg
