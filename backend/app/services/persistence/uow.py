"""UnitOfWork — one session, one short transaction, immediate commit.

This is the ONLY way persistence workers (and any future producer that needs
to write inline) should obtain a DB session.  Guarantees:

  * Session lifetime is bounded by the callable's execution.
  * Transaction is opened by ``async with session.begin()`` and committed
    automatically on return (or rolled back on exception).
  * NO HTTP / Redis / external I/O may happen inside ``fn``.
  * Retries only on transient errors (``OperationalError``, asyncpg
    connection-level errors, lock-not-available).  Integrity / data errors
    bubble out unchanged because they signal a producer bug.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    OperationalError,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_BACKOFF_S = 0.05  # 50 ms


def _is_transient(exc: BaseException) -> bool:
    """Return True if *exc* should trigger a retry inside ``run_uow``."""
    if isinstance(exc, IntegrityError):
        return False
    if isinstance(exc, OperationalError):
        return True
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    # asyncpg lock_not_available / deadlock surface as DBAPIError subclasses.
    msg = str(exc).lower()
    if "lock" in msg and ("timeout" in msg or "not available" in msg):
        return True
    if "deadlock" in msg:
        return True
    if "connection" in msg and ("reset" in msg or "closed" in msg):
        return True
    return False


async def run_uow(
    fn: Callable[[AsyncSession], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_backoff_s: float = DEFAULT_BASE_BACKOFF_S,
    on_retry: Callable[[BaseException, int], None] | None = None,
) -> T:
    """Execute ``fn(session)`` inside a fresh session + transaction.

    Uses the pooled ``AsyncSessionLocal`` factory — callers running inside a
    Celery worker event loop should still use this helper because the
    persistence workers always run inside the FastAPI/uvicorn event loop
    (Celery tasks just enqueue messages).

    Raises the last exception if every retry attempt fails.
    """
    # Local import to keep ``persistence`` package importable before the
    # database module is fully initialised (e.g. during alembic offline runs).
    from ...database import AsyncSessionLocal

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    return await fn(session)
        except BaseException as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_transient(exc) or attempt >= max_attempts:
                raise
            if on_retry is not None:
                try:
                    on_retry(exc, attempt)
                except Exception:  # pragma: no cover — diagnostic only
                    pass
            backoff = base_backoff_s * (2 ** (attempt - 1))
            backoff += random.uniform(0, backoff)  # full jitter
            logger.debug(
                "[persistence-uow] retry %d/%d after %.0fms: %s",
                attempt, max_attempts, backoff * 1000, exc,
            )
            await asyncio.sleep(backoff)
    # Defensive — should be unreachable.
    assert last_exc is not None
    raise last_exc
