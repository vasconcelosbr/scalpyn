from logging.config import fileConfig
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection
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
    # lock_timeout and statement_timeout are applied at connection level via
    # asyncpg server_settings in run_async_migrations — more reliable than
    # SET LOCAL here (which depends on alembic's internal transaction state
    # and was silently inert in production with asyncpg + run_sync).
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
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


def run_migrations_online() -> None:
    db_url, connect_args = _resolve_db_url(settings.DATABASE_URL)
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    connect_args["options"] = "-c lock_timeout=10000 -c statement_timeout=60000"
    connectable = create_engine(db_url, poolclass=pool.NullPool, connect_args=connect_args)
    with connectable.connect() as connection:
        do_run_migrations(connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
