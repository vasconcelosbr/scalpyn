"""Online pre-apply/verify for the migration 205 partial unique index."""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


INDEX_NAME = "ux_shadow_l3_rejected_consolidated_active"
INDEX_SQL = f"""
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
    ON shadow_trades (user_id, symbol, direction)
 WHERE source = 'L3_REJECTED'
   AND l3_consolidation_enforced = TRUE
   AND status IN ('PENDING', 'RUNNING')
"""


def _url() -> str:
    value = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_PUBLIC_URL_or_DATABASE_URL_missing")
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        value = "postgresql+asyncpg://" + value[len("postgresql://") :]
    return value


async def main(apply: bool) -> None:
    engine = create_async_engine(_url(), isolation_level="AUTOCOMMIT")
    evidence: dict[str, object] = {"mode": "APPLY" if apply else "DRY_RUN"}
    try:
        async with engine.connect() as connection:
            duplicate_groups = await connection.scalar(
                text(
                    """
                    SELECT COUNT(*)
                      FROM (
                            SELECT user_id, symbol, direction
                              FROM shadow_trades
                             WHERE source = 'L3_REJECTED'
                               AND l3_consolidation_enforced IS TRUE
                               AND status IN ('PENDING', 'RUNNING')
                             GROUP BY user_id, symbol, direction
                            HAVING COUNT(*) > 1
                           ) AS duplicate_keys
                    """
                )
            )
            evidence["canonical_duplicate_groups_before"] = int(
                duplicate_groups or 0
            )
            if duplicate_groups:
                raise RuntimeError("canonical_duplicate_groups_present")
            if apply:
                await connection.execute(text(INDEX_SQL))

            row = (
                await connection.execute(
                    text(
                        """
                        SELECT i.indisvalid,
                               i.indisready,
                               pg_get_indexdef(i.indexrelid) AS indexdef
                          FROM pg_index AS i
                          JOIN pg_class AS c ON c.oid = i.indexrelid
                          JOIN pg_namespace AS n ON n.oid = c.relnamespace
                         WHERE n.nspname = 'public'
                           AND c.relname = :index_name
                        """
                    ),
                    {"index_name": INDEX_NAME},
                )
            ).mappings().first()
            evidence["index"] = dict(row) if row else None
            if apply and (
                row is None or not row["indisvalid"] or not row["indisready"]
            ):
                raise RuntimeError("preapplied_index_not_ready_and_valid")
    finally:
        await engine.dispose()
    print(json.dumps(evidence, default=str, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
