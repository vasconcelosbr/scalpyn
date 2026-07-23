"""Anthropic model discovery and verification for Profile Intelligence."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .ai_keys_service import get_decrypted_api_key
from .profile_intelligence_analysis_v2 import ANALYSIS_SKILL_VERSION


DEFAULT_AI_MODEL = "claude-haiku-4-5-20251001"
SUPPORTED_AI_MODELS: dict[str, dict[str, Any]] = {
    "claude-fable-5": {
        "label": "Claude Fable 5",
        "positioning": "Maior capacidade para agentes e código",
    },
    "claude-opus-4-8": {
        "label": "Claude Opus 4.8",
        "positioning": "Alta capacidade e raciocínio profundo",
    },
    "claude-sonnet-5": {
        "label": "Claude Sonnet 5",
        "positioning": "Equilíbrio entre capacidade e velocidade",
    },
    DEFAULT_AI_MODEL: {
        "label": "Claude Haiku 4.5",
        "positioning": "Menor latência e modelo padrão",
    },
}


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    return str(value)


def _safe_provider_error(exc: Exception) -> tuple[str, str]:
    name = exc.__class__.__name__.lower()
    status = getattr(exc, "status_code", None)
    request_id = str(getattr(exc, "request_id", "") or "")
    if status == 401 or "authentication" in name:
        return "UNAUTHORIZED", request_id
    if status == 429 or "ratelimit" in name:
        return "RATE_LIMITED", request_id
    if "timeout" in name:
        return "TIMEOUT", request_id
    if status == 404 or "notfound" in name:
        return "UNAVAILABLE", request_id
    return "PROVIDER_ERROR", request_id


async def _client(db: AsyncSession, user_id: UUID, timeout_seconds: float = 30.0):
    api_key = await get_decrypted_api_key(db, user_id, "anthropic")
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("anthropic_key_not_configured")
    import anthropic  # type: ignore

    return anthropic.AsyncAnthropic(
        api_key=api_key,
        timeout=max(10.0, min(timeout_seconds, 60.0)),
        max_retries=0,
    )


def configured_model(settings: Mapping[str, Any] | None) -> str:
    value = str((settings or {}).get("ai_model") or DEFAULT_AI_MODEL)
    return value if value in SUPPORTED_AI_MODELS else DEFAULT_AI_MODEL


async def list_models_for_key(
    db: AsyncSession,
    user_id: UUID,
) -> dict[str, Any]:
    """List only the allow-listed models and their key-specific availability."""
    client = await _client(db, user_id)
    try:
        page = await client.models.list(limit=100)
        raw_models = list(getattr(page, "data", None) or [])
        by_id = {str(getattr(item, "id", "")): item for item in raw_models}
        request_id = str(getattr(page, "_request_id", "") or "")
        items = []
        for model_id, metadata in SUPPORTED_AI_MODELS.items():
            item = by_id.get(model_id)
            dumped = _plain(item) if item is not None else {}
            items.append({
                "id": model_id,
                **metadata,
                "status": "AVAILABLE" if item is not None else "UNAVAILABLE",
                "available": item is not None,
                "capabilities": (dumped or {}).get("capabilities") or {},
                "max_input_tokens": (dumped or {}).get("max_input_tokens"),
                "max_tokens": (dumped or {}).get("max_tokens"),
                "display_name": (dumped or {}).get("display_name"),
                "created_at": (dumped or {}).get("created_at"),
            })
        return {
            "provider": "anthropic",
            "models": items,
            "request_id": request_id or None,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        }
    except Exception as exc:
        status, request_id = _safe_provider_error(exc)
        return {
            "provider": "anthropic",
            "models": [{
                "id": model_id,
                **metadata,
                "status": status,
                "available": False,
                "capabilities": {},
                "max_input_tokens": None,
                "max_tokens": None,
                "display_name": None,
                "created_at": None,
            } for model_id, metadata in SUPPORTED_AI_MODELS.items()],
            "request_id": request_id or None,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "analysis_skill_version": ANALYSIS_SKILL_VERSION,
            "error_code": status,
        }
    finally:
        await client.close()


async def retrieve_model_for_key(
    db: AsyncSession,
    user_id: UUID,
    model_id: str,
) -> dict[str, Any]:
    if model_id not in SUPPORTED_AI_MODELS:
        raise ValueError("unsupported_profile_intelligence_ai_model")
    client = await _client(db, user_id)
    try:
        item = await client.models.retrieve(model_id)
        dumped = _plain(item) or {}
        returned_id = str(dumped.get("id") or getattr(item, "id", "") or "")
        if returned_id != model_id:
            raise ValueError("anthropic_model_identity_mismatch")
        return {
            "provider": "anthropic",
            "model_id": model_id,
            "status": "AVAILABLE",
            "available": True,
            "capabilities": dumped.get("capabilities") or {},
            "max_input_tokens": dumped.get("max_input_tokens"),
            "max_tokens": dumped.get("max_tokens"),
            "display_name": dumped.get("display_name"),
            "request_id": str(getattr(item, "_request_id", "") or "") or None,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
    except ValueError:
        raise
    except Exception as exc:
        status, request_id = _safe_provider_error(exc)
        return {
            "provider": "anthropic",
            "model_id": model_id,
            "status": status,
            "available": False,
            "capabilities": {},
            "request_id": request_id or None,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        await client.close()

