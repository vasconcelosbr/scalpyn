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

engine = create_async_engine(
    _db_url,
    echo=False,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_recycle=1800,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# ── Celery-safe session factory ───────────────────────────────────────────────
# Celery workers create a NEW event loop per task via asyncio.new_event_loop().
# asyncpg connection pools are bound to a specific event loop — reusing a pool
# across loops raises "Future attached to a different loop".
# NullPool disables connection reuse: each session opens/closes its own
# connection within the task's event loop, fully avoiding this error.
_celery_engine = create_async_engine(
    _db_url,
    connect_args=_connect_args,
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
