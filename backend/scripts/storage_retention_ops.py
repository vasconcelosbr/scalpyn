"""Explicit one-time provisioning and bounded retention operations.

Run from backend with PYTHONPATH=. and DATABASE_URL in the environment.
Index construction is deliberately outside application startup migrations.
"""
import argparse
import asyncio
import json
from uuid import UUID

import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import settings
from app.schemas.storage_retention import StorageRetentionConfig
from app.services.storage_retention import run_retention

INDEXES = {
    "ix_retention_ingestion_ok": "ON ohlcv_state_ingestion_observations(observed_at,symbol,timeframe,capture_contract_version) WHERE status='ok' AND error_code IS NULL AND rejected_from_closed_rows=0",
    "ix_retention_completed_labels": "ON pump_opportunity_label_queue(user_id,completed_at,observation_id,label_spec_hash,horizon_minutes) WHERE completed_at IS NOT NULL AND resource_block IS NULL",
}


async def main(args):
    c = await asyncpg.connect(settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        if args.action == "indexes":
            await c.execute("SET statement_timeout='180s'")
            await c.execute("SET lock_timeout='5s'")
            for name, definition in INDEXES.items():
                valid = await c.fetchval("SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass($1)", name)
                if valid is False:
                    await c.execute(f"DROP INDEX CONCURRENTLY {name}")
                if valid is not True:
                    await c.execute(f"CREATE INDEX CONCURRENTLY {name} {definition}")
                print(json.dumps({"index": name, "valid": await c.fetchval("SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass($1)", name)}), flush=True)
            return
        owner = UUID(args.owner)
        if args.action == "provision":
            if not args.authorization:
                raise ValueError("authorization text required")
            if not await c.fetchval("SELECT EXISTS(SELECT 1 FROM users WHERE id=$1)", owner):
                raise ValueError("unknown owner")
            await c.execute("INSERT INTO storage_retention_owners(user_id,authorization_reason) VALUES($1,$2) ON CONFLICT DO NOTHING", owner, args.authorization)
            return
        if args.action == "configure":
            from pathlib import Path
            from app.services.config_service import config_service
            cfg = StorageRetentionConfig.model_validate_json(Path(args.config).read_text())
            engine = create_async_engine(settings.DATABASE_URL)
            try:
                async with async_sessionmaker(engine)() as db:
                    await config_service.update_config(db, "storage_retention", owner, cfg.model_dump(), owner,
                                                       change_description=args.authorization or "Authorized storage retention rollout")
                print(cfg.model_dump_json())
            finally:
                await engine.dispose()
            return
        print(json.dumps(await run_retention(c, owner, force=True, preview=args.action == "preview"), default=str))
    finally:
        await c.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("indexes", "provision", "configure", "preview", "run"))
    parser.add_argument("--owner")
    parser.add_argument("--authorization")
    parser.add_argument("--config")
    asyncio.run(main(parser.parse_args()))
