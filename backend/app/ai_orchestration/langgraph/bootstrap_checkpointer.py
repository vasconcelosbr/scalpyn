"""Explicit, idempotent setup for LangGraph's internal PostgreSQL tables."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
import json

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from .checkpoint import checkpoint_connection_string, strict_serializer
from .config import get_langgraph_settings


async def bootstrap(raw_url: str | None = None) -> dict:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    settings = get_langgraph_settings()
    if not settings.strict_msgpack:
        raise RuntimeError("LANGGRAPH_STRICT_MSGPACK_REQUIRED")
    conninfo = checkpoint_connection_string(raw_url)
    async with await AsyncConnection.connect(
        conninfo,
        autocommit=True,
        row_factory=dict_row,
    ) as connection:
        row = await (await connection.execute("SHOW search_path")).fetchone()
        observed = str(row["search_path"]).replace('"', "")
        observed_schemas = [part.strip() for part in observed.split(",")]
        expected_schemas = [settings.checkpoint_schema, "public"]
        if observed_schemas != expected_schemas:
            raise RuntimeError(f"CHECKPOINTER_SEARCH_PATH_INVALID: {observed}")

    async with AsyncPostgresSaver.from_conn_string(
        conninfo,
        serde=strict_serializer(),
    ) as saver:
        await saver.setup()

    completed_at = datetime.now(timezone.utc)
    metadata = {
        "status": "COMPLETED",
        "schema": settings.checkpoint_schema,
        "strict_msgpack": True,
        "langgraph_version": version("langgraph"),
        "checkpoint_postgres_version": version("langgraph-checkpoint-postgres"),
        "psycopg_version": version("psycopg"),
        "completed_at": completed_at.isoformat(),
    }
    async with await AsyncConnection.connect(
        conninfo,
        autocommit=True,
        row_factory=dict_row,
    ) as connection:
        await connection.execute(
            """
            INSERT INTO public.ai_graph_runtime_metadata (metadata_key, metadata_value, created_at, updated_at)
            VALUES ('checkpointer_setup', %s::jsonb, %s, %s)
            ON CONFLICT (metadata_key) DO UPDATE
            SET metadata_value = EXCLUDED.metadata_value, updated_at = EXCLUDED.updated_at
            """,
            (json.dumps(metadata), completed_at, completed_at),
        )
    return metadata


def main() -> None:
    print(json.dumps(asyncio.run(bootstrap()), sort_keys=True))


if __name__ == "__main__":
    main()
