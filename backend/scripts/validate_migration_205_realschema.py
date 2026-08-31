"""Exercise migration 205 on an isolated schema in a real PostgreSQL DB."""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


PREFIX = "migration_test_205_"
INDEX_NAME = "ux_shadow_l3_rejected_consolidated_active"
INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
    ON shadow_trades (user_id, symbol, direction)
 WHERE source = 'L3_REJECTED'
   AND l3_consolidation_enforced = TRUE
   AND status IN ('PENDING', 'RUNNING')
"""


def _async_url(value: str) -> str:
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        value = "postgresql+asyncpg://" + value[len("postgresql://") :]
    return value


async def main() -> None:
    source_url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get(
        "DATABASE_URL"
    )
    if not source_url:
        raise RuntimeError("DATABASE_PUBLIC_URL_or_DATABASE_URL_missing")
    schema_name = PREFIX + uuid4().hex[:12]
    if not schema_name.startswith(PREFIX):
        raise RuntimeError("unsafe_temporary_schema_name")

    engine = create_async_engine(_async_url(source_url))
    evidence: dict[str, object] = {
        "schema": schema_name,
        "created": False,
        "dropped": False,
    }
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            evidence["created"] = True
            await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}"'))
            await connection.execute(
                text(
                    """
                    CREATE TABLE shadow_trades (
                        id UUID PRIMARY KEY,
                        user_id UUID NOT NULL,
                        symbol TEXT NOT NULL,
                        direction TEXT,
                        source TEXT NOT NULL,
                        status TEXT NOT NULL,
                        l3_consolidation_enforced BOOLEAN NOT NULL DEFAULT FALSE
                    )
                    """
                )
            )
            await connection.execute(text(INDEX_SQL))
            await connection.execute(text(INDEX_SQL))
            evidence["idempotent_upgrade"] = True
            evidence["indexdef"] = await connection.scalar(
                text(
                    """
                    SELECT indexdef
                      FROM pg_indexes
                     WHERE schemaname = :schema
                       AND indexname = :index_name
                    """
                ),
                {"schema": schema_name, "index_name": INDEX_NAME},
            )

            owner = uuid4()
            legacy_values = {
                "owner": owner,
                "first": uuid4(),
                "second": uuid4(),
            }
            await connection.execute(
                text(
                    """
                    INSERT INTO shadow_trades (
                        id, user_id, symbol, direction, source, status,
                        l3_consolidation_enforced
                    ) VALUES
                        (:first, :owner, 'BTC_USDT', 'SPOT', 'L3_REJECTED',
                         'RUNNING', FALSE),
                        (:second, :owner, 'BTC_USDT', 'SPOT', 'L3_REJECTED',
                         'RUNNING', FALSE)
                    """
                ),
                legacy_values,
            )
            evidence["legacy_duplicate_rows_allowed"] = 2

            await connection.execute(
                text(
                    """
                    INSERT INTO shadow_trades (
                        id, user_id, symbol, direction, source, status,
                        l3_consolidation_enforced
                    ) VALUES (
                        :id, :owner, 'ETH_USDT', 'SPOT', 'L3_REJECTED',
                        'RUNNING', TRUE
                    )
                    """
                ),
                {"id": uuid4(), "owner": owner},
            )
            duplicate_blocked = False
            try:
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO shadow_trades (
                                id, user_id, symbol, direction, source, status,
                                l3_consolidation_enforced
                            ) VALUES (
                                :id, :owner, 'ETH_USDT', 'SPOT', 'L3_REJECTED',
                                'PENDING', TRUE
                            )
                            """
                        ),
                        {"id": uuid4(), "owner": owner},
                    )
            except Exception as exc:
                duplicate_blocked = (
                    "UniqueViolation" in type(getattr(exc, "orig", exc)).__name__
                    or "duplicate key" in str(exc).lower()
                )
            evidence["canonical_duplicate_blocked"] = duplicate_blocked
            if not duplicate_blocked:
                raise RuntimeError("canonical_duplicate_was_not_blocked")

            await connection.execute(text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
            missing_after_downgrade = await connection.scalar(
                text(
                    """
                    SELECT COUNT(*) = 0
                      FROM pg_indexes
                     WHERE schemaname = :schema
                       AND indexname = :index_name
                    """
                ),
                {"schema": schema_name, "index_name": INDEX_NAME},
            )
            evidence["downgrade_removed_index"] = bool(missing_after_downgrade)
            await connection.execute(text(INDEX_SQL))
            evidence["reupgrade_restored_index"] = bool(
                await connection.scalar(
                    text(
                        """
                        SELECT COUNT(*) = 1
                          FROM pg_indexes
                         WHERE schemaname = :schema
                           AND indexname = :index_name
                        """
                    ),
                    {"schema": schema_name, "index_name": INDEX_NAME},
                )
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
            evidence["dropped"] = True
        await engine.dispose()

    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
