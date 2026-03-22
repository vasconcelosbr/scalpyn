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
    return _fernet().decrypt(enc).decode()


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
        monthly_token_limit=monthly_token_limit,
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


async def test_anthropic_key(db: AsyncSession, user_id: UUID, provider: str = "anthropic") -> Tuple[bool, str]:
    r = await _get_record(db, user_id, provider)
    if not r:
        return False, "No key configured."

    try:
        import anthropic
    except ImportError:
        return False, "anthropic package not installed. Run: pip install anthropic"

    try:
        client = anthropic.Anthropic(api_key=decrypt_value(r.api_key_encrypted))
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "Hi"}],
        )
        r.is_validated = True
        r.test_status = "ok"
        r.test_error = None
        r.last_tested_at = datetime.now(timezone.utc)
        await db.commit()
        return True, "Anthropic API connection established successfully."

    except anthropic.AuthenticationError:
        msg = "Invalid key. Please check your API key."
    except anthropic.PermissionDeniedError:
        msg = "Permission denied. Check the key's scopes."
    except Exception as e:
        msg = f"Unexpected error: {str(e)[:200]}"

    r.is_validated = False
    r.test_status = "error"
    r.test_error = msg
    r.last_tested_at = datetime.now(timezone.utc)
    await db.commit()
    return False, msg
