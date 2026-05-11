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
        try:
            async with session.begin():
                return await fn(session)
        except BaseException:
            # Task #237 — defense in depth. ``async with session.begin()``
            # already auto-rolls-back on exception, but if the rollback
            # itself fails (or if the connection is in a pending-rollback
            # state from a half-committed savepoint), the connection
            # could return to the pool poisoned and raise
            # ``PendingRollbackError`` / ``InFailedSQLTransactionError``
            # on the very next task that picks it up. Force a best-effort
            # rollback before the outer ``async with factory()`` returns
            # the connection to the pool.
            await _safe_rollback(session)
            raise


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
