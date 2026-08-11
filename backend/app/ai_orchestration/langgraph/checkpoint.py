from __future__ import annotations

from contextlib import asynccontextmanager
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import get_langgraph_settings


def checkpoint_connection_string(raw_url: str | None = None) -> str:
    settings = get_langgraph_settings()
    database_url = raw_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the LangGraph checkpointer")
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    database_url = database_url.replace("postgres+asyncpg://", "postgresql://", 1)
    parts = urlsplit(database_url)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("LangGraph checkpointer requires PostgreSQL")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    # SQLAlchemy/asyncpg uses ``ssl`` while psycopg conninfo uses ``sslmode``.
    # Normalize the public staging URI without weakening TLS requirements.
    if "ssl" in query:
        ssl_value = query.pop("ssl")
        query.setdefault("sslmode", ssl_value)
    required_options = f"-csearch_path={settings.checkpoint_schema},public"
    current_options = query.get("options", "")
    if current_options and required_options not in current_options:
        raise RuntimeError("DATABASE_URL contains a conflicting PostgreSQL options value")
    query["options"] = required_options
    query["application_name"] = "scalpyn-langgraph-checkpointer"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def strict_serializer():
    settings = get_langgraph_settings()
    if not settings.strict_msgpack:
        raise RuntimeError("LANGGRAPH_STRICT_MSGPACK_REQUIRED")
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(allowed_msgpack_modules=None)


@asynccontextmanager
async def postgres_checkpointer(raw_url: str | None = None):
    """Open the production checkpointer without running schema setup."""

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conninfo = checkpoint_connection_string(raw_url)
    async with AsyncPostgresSaver.from_conn_string(
        conninfo,
        serde=strict_serializer(),
    ) as saver:
        yield saver
