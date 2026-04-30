import asyncio
import logging

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from fastapi import HTTPException, status
from .config import settings

logger = logging.getLogger(__name__)


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


_db_url, _connect_args = _resolve_db_url(settings.DATABASE_URL)

# Fail fast on unreachable DB — asyncpg default is 60 s which is too long
# for Cloud Run startup.  15 s is enough for Cloud SQL proxy sockets.
_connect_args.setdefault("timeout", 15)
# Add command_timeout to prevent hung queries
_connect_args.setdefault("command_timeout", 60)

engine = create_async_engine(
    _db_url,
    echo=False,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_recycle=1800,
    # Pool sized for Cloud Run + Cloud SQL.  Each Cloud Run instance also has
    # Celery worker + beat sharing the same Cloud SQL.  With min-instances=1
    # and rolling deploys, the OLD and NEW revisions overlap briefly and BOTH
    # hold their pools.  Cloud SQL db-f1-micro caps at 25 connections, db-g1-small
    # at 50.  Previous values (20 + 30 = 50) caused container startup to stall
    # waiting for connections during deploy overlap, so the port never opened.
    # 5 + 5 = 10 per instance × 2 revisions = 20 — fits inside even db-f1-micro.
    pool_size=5,
    max_overflow=5,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# ── Celery-safe session factory ───────────────────────────────────────────────
# Celery workers create a NEW event loop per task via asyncio.new_event_loop().
# asyncpg connection pools are bound to a specific event loop — reusing a pool
# across loops raises "Future attached to a different loop".
# NullPool disables connection reuse: each session opens/closes its own
# connection within the task's event loop, fully avoiding this error.
_celery_connect_args = dict(_connect_args)
_celery_connect_args.setdefault("command_timeout", 60)
_celery_engine = create_async_engine(
    _db_url,
    connect_args=_celery_connect_args,
    poolclass=NullPool,
)
CeleryAsyncSessionLocal = async_sessionmaker(_celery_engine, expire_on_commit=False)

Base = declarative_base()


async def get_db():
    try:
        async with AsyncSessionLocal() as session:
            yield session
    except asyncio.CancelledError:
        logger.error("DB session cancelled (CancelledError) — cold start or pool timeout")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database temporarily unavailable, please retry",
        )
    except HTTPException:
        # Don't mask FastAPI HTTP errors (e.g. 404, 502 from endpoint logic)
        raise
    except Exception as exc:
        logger.error("DB session error: %s: %s", type(exc).__name__, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database error",
        )
    except BaseException as exc:
        logger.error("DB session fatal: %s: %s", type(exc).__name__, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database temporarily unavailable",
        )
