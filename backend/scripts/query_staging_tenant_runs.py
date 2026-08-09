"""Print a secret-free summary of graph runs grouped by tenant."""

from __future__ import annotations

import asyncio
import json
import os

import asyncpg


async def main() -> None:
    database_url = os.environ.get("DATABASE_PUBLIC_URL", os.environ["DATABASE_URL"]).replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(
            """
            SELECT tenant_id::text, COUNT(*)::int AS run_count,
                   MIN(id)::text AS sample_run
              FROM ai_graph_runs
             GROUP BY tenant_id
             ORDER BY tenant_id
            """
        )
    finally:
        await connection.close()
    print(json.dumps([dict(row) for row in rows], separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
