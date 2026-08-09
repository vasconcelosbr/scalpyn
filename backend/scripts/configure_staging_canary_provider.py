"""Securely configure one Anthropic key for the inactive staging canary tenant.

The provider key is accepted only through the process environment, is encrypted
before persistence, and is never printed. Validation uses the authenticated
models catalog and therefore performs no text generation and consumes no model
tokens. The operational wrapper must delete the temporary Railway variable as
soon as this script succeeds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from uuid import UUID

from sqlalchemy import select

from run_alembic_with_railway_proxy import _public_database_url


ENV_KEY = "SCALPYN_STAGING_CANARY_ANTHROPIC_API_KEY"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--monthly-token-limit", required=True, type=int)
    return parser.parse_args()


def _assert_staging() -> str:
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
    if "staging" not in environment.lower():
        raise SystemExit("REFUSED: provider configuration is staging-only")
    return environment


async def main() -> None:
    args = _arguments()
    environment = _assert_staging()
    if args.monthly_token_limit <= 0:
        raise SystemExit("REFUSED: monthly token limit must be positive")
    api_key = os.getenv(ENV_KEY, "").strip()
    if not api_key:
        raise SystemExit(f"REFUSED: secure Railway variable {ENV_KEY} is absent")
    os.environ["DATABASE_URL"] = _public_database_url()

    from app.ai_orchestration.provider_adapters import AnthropicCatalogAdapter
    from app.ai_orchestration.provider_registry import default_registry
    from app.database import AsyncSessionLocal, engine
    import app.models  # noqa: F401
    from app.models.ai_provider_key import AIProviderKey
    from app.models.user import User
    from app.services.ai_keys_service import get_ai_key_info, save_ai_key, test_anthropic_key

    target_model = args.model_id
    default_registry().get_entry("anthropic", target_model)
    async with AsyncSessionLocal() as session:
        tenant = await session.get(User, args.tenant_id)
        if tenant is None:
            raise SystemExit("REFUSED: staging canary tenant does not exist")
        if tenant.is_active:
            raise SystemExit("REFUSED: staging canary tenant must remain inactive")
        existing = (await session.execute(select(AIProviderKey).where(
            AIProviderKey.user_id == args.tenant_id,
            AIProviderKey.provider == "anthropic",
            AIProviderKey.is_active.is_(True),
        ))).scalar_one_or_none()
        if existing is not None:
            raise SystemExit("REFUSED: an active Anthropic key already exists for this tenant")

        await save_ai_key(
            session,
            args.tenant_id,
            "anthropic",
            api_key,
            label="Staging canary only",
            monthly_token_limit=args.monthly_token_limit,
        )
        success, _message = await test_anthropic_key(session, args.tenant_id, "anthropic")
        info = await get_ai_key_info(session, args.tenant_id, "anthropic")
        if not success or not info or not info.get("is_validated"):
            record = (await session.execute(select(AIProviderKey).where(
                AIProviderKey.user_id == args.tenant_id,
                AIProviderKey.provider == "anthropic",
                AIProviderKey.is_active.is_(True),
            ))).scalar_one_or_none()
            if record is not None:
                record.is_active = False
                await session.commit()
            raise SystemExit("PROVIDER_VALIDATION_FAILED")

        model_ids = await AnthropicCatalogAdapter().list_model_ids(api_key=api_key)
        output = {
            "environment": environment,
            "tenant_id": str(args.tenant_id),
            "tenant_active": bool(tenant.is_active),
            "provider": "anthropic",
            "active": bool(info.get("is_active")),
            "validated": bool(info.get("is_validated")),
            "test_status": info.get("test_status"),
            "last_tested_at": info.get("last_tested_at"),
            "monthly_token_limit": info.get("monthly_token_limit"),
            "tokens_used_month": info.get("tokens_used_month"),
            "key_material_printed": False,
            "catalog_model_ids": list(model_ids),
            "target_model_id": target_model,
            "target_model_returned_by_catalog": target_model in model_ids,
            "generation_calls": 0,
        }
        if not output["target_model_returned_by_catalog"]:
            record = (await session.execute(select(AIProviderKey).where(
                AIProviderKey.user_id == args.tenant_id,
                AIProviderKey.provider == "anthropic",
                AIProviderKey.is_active.is_(True),
            ))).scalar_one_or_none()
            if record is not None:
                record.is_active = False
                await session.commit()
            raise SystemExit("TARGET_MODEL_NOT_RETURNED_BY_PROVIDER_CATALOG")
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
