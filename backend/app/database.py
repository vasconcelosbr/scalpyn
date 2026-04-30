import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from fastapi import HTTPException, status
from .config import settings

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read int from env with safe fallback on parse error."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid value for %s (%r) — falling back to %d", name, raw, default)
        return default


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

# ── Pool sizing ──────────────────────────────────────────────────────────────
# Each uvicorn worker process holds its own pool; Celery worker + beat run in
# their own processes alongside.  The total upper bound on simultaneous
# Postgres connections from this app is therefore:
#
#   uvicorn_workers * (DB_POOL_SIZE + DB_MAX_OVERFLOW)
#       + celery_workers (NullPool, ~one connection per active task)
#       + celery_beat
#
# This must stay below the Cloud SQL tier's `max_connections`.  Always run
# `SHOW max_connections;` against the live instance before raising the
# defaults — db-f1-micro = 25, db-g1-small = 50, db-n1-standard-1 = 100.
#
# Defaults bumped to 10 + 10 (was 5 + 5) to give API request handlers,
# WebSocket event handlers and the in-process indicator schedulers
# (structural + microstructure + combined, each at concurrency=8) enough
# headroom on this engine's pool. The pipeline scan loop runs on the
# separate Celery NullPool engine below and does NOT consume from this
# pool, so it is not part of this calculation (Task #116).
_pool_size = _env_int("DB_POOL_SIZE", 10)
_max_overflow = _env_int("DB_MAX_OVERFLOW", 10)
_pool_timeout = _env_int("DB_POOL_TIMEOUT", 30)

engine = create_async_engine(
    _db_url,
    echo=False,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_timeout=_pool_timeout,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

logger.info(
    "DB pool configured: pool_size=%d max_overflow=%d pool_timeout=%ds",
    _pool_size, _max_overflow, _pool_timeout,
)


# ── Pool observability ───────────────────────────────────────────────────────
# Periodic logger so we can correlate `QueuePool limit … reached` errors with
# actual pool utilisation.  Disabled when DB_POOL_STATS_INTERVAL_SECONDS=0.

def log_pool_stats() -> None:
    """Log a snapshot of the engine's connection pool state."""
    try:
        pool = engine.pool
        logger.info(
            "DB pool stats: size=%d checked_out=%d overflow=%d checked_in=%d status=%r",
            pool.size(),
            pool.checkedout(),
            pool.overflow(),
            pool.checkedin(),
            pool.status(),
        )
    except Exception as exc:  # pragma: no cover — diagnostics only
        logger.warning("Failed to log DB pool stats: %s", exc)


async def _pool_stats_loop(interval_seconds: int) -> None:
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            log_pool_stats()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — diagnostics only
            logger.warning("Pool stats loop iteration failed: %s", exc)


def start_pool_stats_logger() -> asyncio.Task | None:
    """Start the periodic pool-stats logger.  Returns the task (or None when disabled)."""
    interval = _env_int("DB_POOL_STATS_INTERVAL_SECONDS", 60)
    if interval <= 0:
        logger.info("DB pool stats logger disabled (DB_POOL_STATS_INTERVAL_SECONDS=%d)", interval)
        return None
    logger.info("Starting DB pool stats logger (every %ds)", interval)
    return asyncio.create_task(_pool_stats_loop(interval), name="db-pool-stats-logger")

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
