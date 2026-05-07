"""Fault-injection harness for DB transaction safety tests.

This module provides two complementary context managers:

``force_failure_once(session, statement_substring)``
    Patches a single ``AsyncSession`` instance so that its next
    ``execute()`` call whose SQL matches *statement_substring* raises a
    synthetic ``OperationalError``.  The patch is removed on exit.
    Use this when you already hold a reference to the session under test
    (e.g. the session yielded by ``get_db()``).

``force_engine_failure_once(sync_engine, statement_substring)``
    Registers a ``before_cursor_execute`` event listener on a sync engine
    that fires once on the next matching statement — whether it comes from
    ``session.execute()``, an ORM flush, a commit-time INSERT, or any
    other code path that routes through that engine.  The listener removes
    itself on exit so subsequent statements on any session run normally.
    Use this when the code under test creates its own sessions internally
    (e.g. ``run_db_task``) or you want to test ORM add/flush isolation.

Quickstart — add a new fault-injection test
--------------------------------------------

.. code-block:: python

    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from db_fault_injection import force_failure_once, force_engine_failure_once
    from app.database import AsyncSessionLocal, engine

    # ── session-level: you hold the session ──────────────────────────────
    async def test_my_route_handles_db_error():
        async with AsyncSessionLocal() as session:
            with force_failure_once(session, statement_substring="UPDATE"):
                with pytest.raises(OperationalError):
                    await session.execute(text("UPDATE foo SET bar=1"))
            # Patch gone; session still usable.
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1

    # ── engine-level: code creates sessions internally ───────────────────
    async def test_run_db_task_survives_injected_failure():
        with force_engine_failure_once(engine.sync_engine, "INSERT"):
            with pytest.raises(OperationalError):
                await run_db_task(lambda db: db.execute(text("INSERT INTO foo VALUES (1)")))
        # Next call on a fresh session (created inside run_db_task) works fine.
        result = await run_db_task(lambda db: db.execute(text("SELECT 42")))

Notes
-----
* Both helpers are safe to nest — each manages its own listener/patch
  independently.
* Neither helper touches production code; they are test-only utilities.
* ``force_engine_failure_once`` fires on **all** sessions bound to the
  given engine.  Use a dedicated test engine (or scope the substring
  tightly) when other sessions may be active concurrently.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from sqlalchemy import event
from sqlalchemy.exc import OperationalError


# ── session-level harness ─────────────────────────────────────────────────────

@contextmanager
def force_failure_once(session, statement_substring: Optional[str] = None):
    """Arm *session* to raise ``OperationalError`` on the next matching execute.

    Args:
        session:              An ``AsyncSession`` instance to patch.
        statement_substring:  If given, only triggers when this string appears
                              in the stringified SQL statement.  If ``None``,
                              the *first* execute call is always intercepted.

    The patch is applied to the individual session instance, not the class, so
    other sessions opened from the same engine are unaffected.

    On exit (normal or exceptional) the original execute method is restored.

    Example::

        with force_failure_once(session, "UPDATE watchlists"):
            with pytest.raises(OperationalError):
                await session.execute(text("UPDATE watchlists SET foo=1"))
        # session still works here
    """
    original_execute = session.execute
    fired = False

    async def _patched_execute(stmt, *args, **kwargs):
        nonlocal fired
        if not fired:
            stmt_text = str(stmt.text) if hasattr(stmt, "text") else str(stmt)
            if statement_substring is None or statement_substring in stmt_text:
                fired = True
                raise OperationalError(
                    "Injected fault by force_failure_once",
                    params={},
                    orig=Exception("synthetic DB failure"),
                )
        return await original_execute(stmt, *args, **kwargs)

    session.execute = _patched_execute
    try:
        yield
    finally:
        session.execute = original_execute


# ── engine-level harness ──────────────────────────────────────────────────────

@contextmanager
def force_engine_failure_once(sync_engine, statement_substring: Optional[str] = None):
    """Register a one-shot ``before_cursor_execute`` listener on *sync_engine*.

    The listener fires the next time any statement matching
    *statement_substring* is executed through the engine (regardless of which
    session or code path emits it) and raises ``OperationalError``.  After
    firing once, the listener removes itself so subsequent statements succeed.
    The listener is also removed unconditionally on context-manager exit.

    This covers paths that ``force_failure_once`` cannot reach:
    * ORM flush / commit-time DML (``session.add`` + ``session.commit``)
    * Sessions created internally by the code under test (e.g. ``run_db_task``)
    * Any helper that bypasses ``AsyncSession.execute`` directly

    Args:
        sync_engine:          A SQLAlchemy ``Engine`` (sync).  For an async
                              engine, pass ``async_engine.sync_engine``.
        statement_substring:  If given, only triggers on statements containing
                              this string.  If ``None``, the very first
                              statement is intercepted.

    Example::

        with force_engine_failure_once(engine.sync_engine, "INSERT"):
            with pytest.raises(OperationalError):
                await run_db_task(lambda db: db.execute(text("INSERT INTO t VALUES (1)")))
        # The engine is clean — all subsequent sessions work normally.
    """
    fired = False

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal fired
        if not fired:
            if statement_substring is None or statement_substring in statement:
                fired = True
                raise OperationalError(
                    "Injected engine-level fault by force_engine_failure_once",
                    params={},
                    orig=Exception("synthetic DB failure"),
                )

    event.listen(sync_engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield
    finally:
        if event.contains(sync_engine, "before_cursor_execute", _before_cursor_execute):
            event.remove(sync_engine, "before_cursor_execute", _before_cursor_execute)
