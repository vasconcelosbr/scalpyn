import asyncio
import logging
import os
from typing import Any, Callable

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
#   WEB_CONCURRENCY * (DB_POOL_SIZE + DB_MAX_OVERFLOW)
#       + CELERY_CONCURRENCY  (NullPool, ~one connection per active task slot)
#       + 1                   (celery beat)
#
# See docs/db-pool-budget.md for the current production numbers and Cloud SQL
# tier comparison.  This must stay below the Cloud SQL tier's `max_connections`.
# Always run `SHOW max_connections;` against the live instance before raising
# the defaults — db-f1-micro = 25, db-g1-small = 50, db-n1-standard-1 = 100.
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

_uvicorn_workers = _env_int("WEB_CONCURRENCY", 2)  # matches Dockerfile runtime stage ENV and start.sh
# Celery uses NullPool — each active task slot opens one connection.
# CELERY_CONCURRENCY matches the --concurrency flag in start.sh (default 1).
# CELERY_WORKERS here means "number of Celery task slots", not process count.
_celery_concurrency = _env_int("CELERY_CONCURRENCY", 1)
_celery_beat = 1  # beat always holds at most 1 connection
_connection_budget = (
    _uvicorn_workers * (_pool_size + _max_overflow)
    + _celery_concurrency
    + _celery_beat
)

logger.info(
    "DB pool configured: pool_size=%d max_overflow=%d pool_timeout=%ds | "
    "connection budget: uvicorn_workers=%d × (pool_size+max_overflow)=%d "
    "+ celery_concurrency=%d + beat=%d = %d total ceiling",
    _pool_size, _max_overflow, _pool_timeout,
    _uvicorn_workers, _pool_size + _max_overflow,
    _celery_concurrency, _celery_beat, _connection_budget,
)


# ── Pool observability ───────────────────────────────────────────────────────
# Periodic logger so we can correlate `QueuePool limit … reached` errors with
# actual pool utilisation.  Disabled when DB_POOL_STATS_INTERVAL_SECONDS=0.

# Track saturation state to warn only on transitions (not every tick).
_pool_saturated: bool = False
_pool_overflow_exhausted: bool = False


def log_pool_stats() -> None:
    """Log a snapshot of the engine's connection pool state.

    Emits a WARNING (once per transition) when:
      - checked_out >= pool_size  (pool is saturated, overflow in use)
      - overflow == max_overflow  (all overflow slots consumed, next request will block/timeout)
    """
    global _pool_saturated, _pool_overflow_exhausted
    try:
        pool = engine.pool
        checked_out = pool.checkedout()
        overflow = pool.overflow()
        logger.info(
            "DB pool stats: size=%d checked_out=%d overflow=%d checked_in=%d status=%r",
            pool.size(),
            checked_out,
            overflow,
            pool.checkedin(),
            pool.status(),
        )

        # Warn on transition into pool-saturated state (overflow is being used).
        now_saturated = checked_out >= _pool_size
        if now_saturated and not _pool_saturated:
            logger.warning(
                "DB pool SATURATED: checked_out=%d >= pool_size=%d — overflow connections in use "
                "(budget ceiling: %d)",
                checked_out, _pool_size, _connection_budget,
            )
        _pool_saturated = now_saturated

        # Warn on transition into overflow-exhausted state (next request will block/timeout).
        now_overflow_exhausted = overflow >= _max_overflow
        if now_overflow_exhausted and not _pool_overflow_exhausted:
            logger.warning(
                "DB pool OVERFLOW EXHAUSTED: overflow=%d >= max_overflow=%d — next requests will "
                "block until pool_timeout=%ds expires (budget ceiling: %d)",
                overflow, _max_overflow, _pool_timeout, _connection_budget,
            )
        _pool_overflow_exhausted = now_overflow_exhausted

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

# ── Session lifecycle — three valid patterns ──────────────────────────────────
#
# (a) FastAPI request handler
#       Use the ``get_db`` dependency.  It yields a session, rolls back on any
#       exception, and closes the connection before the response is sent.
#       Route handlers must still call ``await db.commit()`` after mutations.
#
# (b) Celery task (sync wrapper calling asyncio.run / loop.run_until_complete)
#       Use ``run_db_task(fn, celery=True)``.  This opens a session from the
#       NullPool ``CeleryAsyncSessionLocal`` factory (safe across event loops),
#       wraps execution in ``async with session.begin()`` (auto-commit on
#       success, auto-rollback on error), then closes the session.
#       Never call ``await session.commit()`` or ``await session.rollback()``
#       inside the callback — let the context manager handle it.
#
# (c) Ad-hoc background coroutine (asyncio task in the uvicorn event loop)
#       Use ``run_db_task(fn, celery=False)`` (the default).  Behaviour is
#       identical to (b) but uses the regular pooled engine so uvicorn workers
#       share connections efficiently.
#
# The helper below is the single documented entry-point for (b) and (c).
# Do NOT add new bare ``async with AsyncSessionLocal() as db: … await db.commit()``
# blocks to services or tasks — always go through ``run_db_task``.
# ─────────────────────────────────────────────────────────────────────────────


async def run_db_task(fn: Callable, *, celery: bool = False) -> Any:
    """Open a session, run *fn(session)* inside a transaction, then close.

    Commit is automatic on success; rollback is automatic on any exception.
    The exception is re-raised so callers can handle / log it.

    Args:
        fn:     An async callable that accepts a single ``AsyncSession`` arg.
        celery: When True, use ``CeleryAsyncSessionLocal`` (NullPool — safe
                inside ``asyncio.run()`` / Celery worker event loops).
                When False (default), use ``AsyncSessionLocal`` (pooled —
                correct for coroutines running inside the uvicorn event loop).

    Example::

        async def _write(db: AsyncSession) -> None:
            db.add(MyModel(name="x"))

        await run_db_task(_write)                   # uvicorn background task
        await run_db_task(_write, celery=True)       # Celery async helper
    """
    factory = CeleryAsyncSessionLocal if celery else AsyncSessionLocal
    async with factory() as session:
        async with session.begin():
            return await fn(session)


async def _safe_rollback(session) -> None:
    """Best-effort rollback so a poisoned transaction never survives back into
    the connection pool.  asyncpg raises InFailedSQLTransactionError on every
    subsequent statement until rollback is called.  Failures here are logged
    but never re-raised — the surrounding ``async with AsyncSessionLocal()``
    will close the session and discard the broken connection from the pool.

    Note: ``run_db_task`` and ``async with session.begin()`` blocks handle
    rollback automatically.  This helper is only used by ``get_db`` below.
    """
    try:
        await session.rollback()
    except Exception as rollback_exc:
        logger.warning("Rollback failed: %s: %s", type(rollback_exc).__name__, rollback_exc)


async def get_db():
    """FastAPI DB dependency — pattern (a) in the session lifecycle note above.

    Always rolls back on any exception raised by the route — including
    HTTPException and CancelledError — *before* the connection is returned
    to the pool.  Without this, asyncpg's ``InFailedSQLTransactionError``
    cascades to the next caller that picks up the same connection.

    Route handlers that mutate data must still call ``await db.commit()``
    explicitly — this dependency does not auto-commit.
    """
    try:
        async with AsyncSessionLocal() as session:
            try:
                yield session
            except BaseException:
                # Catch *everything* (HTTPException, CancelledError,
                # SQLAlchemyError, …) so the rollback runs while the session
                # is still open. The exception is then re-raised and handled
                # below for status-code mapping.
                await _safe_rollback(session)
                raise
    except HTTPException:
        # Routes raise these intentionally — propagate as-is.
        raise
    except asyncio.CancelledError:
        logger.error("DB session cancelled (CancelledError) — cold start or pool timeout")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database temporarily unavailable, please retry",
        )
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
