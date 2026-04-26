import asyncio
from logging.config import fileConfig
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

from app.database import Base
from app.config import settings

# Import all models here to register them with Base.metadata
from app.models import *

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # SET commands must live INSIDE begin_transaction so they share the
    # same transaction that alembic manages.  Calling connection.execute()
    # BEFORE context.configure()/begin_transaction() triggers SQLAlchemy
    # 2.0 "autobegin" — an implicit transaction is created, and when
    # alembic then calls begin_transaction() it gets a conflict
    # (InvalidRequestError / nested-transaction mismatch), causing
    # alembic to exit non-zero.  All retries in start.sh fail quickly
    # (~1:56 total) and Cloud Run rolls back.
    #
    # Defense against lock contention during Cloud Run deploy:
    #   SET LOCAL lock_timeout  — fails the migration in 10s if the OLD
    #     revision (Celery beat) is holding AccessExclusiveLock on the
    #     table.  start.sh retries with backoff so the lock can drain.
    #   SET LOCAL statement_timeout — caps any single DDL statement at
    #     60s so a runaway table-rewrite never pins the whole boot.
    from sqlalchemy import text as _sa_text
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        connection.execute(_sa_text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(_sa_text("SET LOCAL statement_timeout = '60s'"))
        context.run_migrations()


def _resolve_db_url(url: str) -> tuple[str, dict]:
    """
    asyncpg does not honour ?host=/path in the URL query string for Unix sockets.
    Extract it and return it as a connect_arg instead.
    """
    connect_args: dict = {}
    if "?" not in url:
        return url, connect_args

    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    if "host" in params and params["host"][0].startswith("/"):
        connect_args["host"] = params["host"][0]
        del params["host"]
        new_query = urlencode({k: v[0] for k, v in params.items()})
        url = urlunparse(parsed._replace(query=new_query))

    return url, connect_args


async def run_async_migrations() -> None:
    db_url, connect_args = _resolve_db_url(settings.DATABASE_URL)
    config.set_main_option("sqlalchemy.url", db_url)

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={**connect_args, "timeout": 20},  # fail fast — don't block Cloud Run startup
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
