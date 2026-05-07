"""Regression tests for Task #122 — InFailedSQLTransactionError flood.

These tests lock in the contract that any per-item failure inside a
WebSocket batch handler, the pipeline scan helper, or the FastAPI
``get_db`` dependency must NOT poison the next statement on the same
session or the next caller in the connection pool.

Background: with asyncpg, once any statement fails inside an open
transaction, every subsequent statement on that connection raises
``InFailedSQLTransactionError`` until ``rollback()`` is called.  Several
backend code paths used to swallow exceptions without rolling back, so a
single bad WS event caused 485+ cascading errors in production.

Tests use plain ``asyncio.run`` to match the existing convention in
``tests/test_coinmarketcap_service.py`` (the project does not depend on
pytest-asyncio).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException, status  # noqa: E402

from app.database import AsyncSessionLocal, _safe_rollback, engine, get_db  # noqa: E402
from app.tasks.pipeline_scan import _update_last_scanned  # noqa: E402
from app.websocket.event_handlers import _process_batch  # noqa: E402


def _run(coro_factory):
    """Run an async test body with a fresh asyncio loop AND a clean
    connection pool — disposing the engine at the end avoids stray
    asyncpg connections being garbage-collected against a closed loop
    (which produces the noisy "Event loop is closed" RuntimeWarning that
    pytest's unraisableexception plugin promotes to a hard failure)."""
    async def _wrapper():
        try:
            await coro_factory()
        finally:
            await engine.dispose()

    asyncio.run(_wrapper())


# ── _safe_rollback recovers a poisoned session ────────────────────────────────

def test_safe_rollback_recovers_failed_transaction():
    """A bad statement aborts the transaction; _safe_rollback must clear it
    so the next statement on the same session succeeds.  Without rollback
    the next statement would raise InFailedSQLTransactionError."""
    async def run():
        async with AsyncSessionLocal() as session:
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(text("SELECT * FROM table_that_does_not_exist_xyz"))

            await _safe_rollback(session)

            row = (await session.execute(text("SELECT 1"))).scalar_one()
            assert row == 1

    _run(run)


def test_safe_rollback_swallows_rollback_errors():
    """_safe_rollback must never raise — broken sessions must be silently
    handled so the surrounding ``async with`` can still close cleanly and
    the connection is discarded by the pool."""
    class _DeadSession:
        async def rollback(self):
            raise RuntimeError("connection lost")

    asyncio.run(_safe_rollback(_DeadSession()))


# ── WebSocket batch handler isolates per-item failures ────────────────────────

def test_process_batch_isolates_single_bad_item():
    """One failing item in a WS batch must NOT abort the other items.

    With per-item SAVEPOINTs, a row that violates a constraint (or any
    other error) is rolled back individually while the rest of the batch
    still commits.  Before the fix, the whole batch was rolled back.
    """
    table_name = f"_t122_items_{uuid.uuid4().hex[:8]}"
    written: list[int] = []

    async def setup():
        async with AsyncSessionLocal() as session:
            await session.execute(text(f"CREATE TABLE {table_name} (id INT PRIMARY KEY)"))
            await session.commit()

    async def teardown():
        async with AsyncSessionLocal() as session:
            await session.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            await session.commit()

    async def process_one(session, item: dict) -> None:
        if item["id"] == 2:
            raise ValueError("bad item by design")
        await session.execute(
            text(f"INSERT INTO {table_name} (id) VALUES (:i)"),
            {"i": item["id"]},
        )
        written.append(item["id"])

    async def verify_persisted() -> list[int]:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                text(f"SELECT id FROM {table_name} ORDER BY id")
            )).scalars().all()
            return list(rows)

    async def run():
        await setup()
        try:
            await _process_batch(
                "test_batch",
                [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}],
                process_one,
                lambda i: i["id"],
            )
            persisted = await verify_persisted()
            assert written == [1, 3, 4]
            assert persisted == [1, 3, 4]
        finally:
            await teardown()

    _run(run)


def test_process_batch_no_cascade_after_db_failure():
    """After a per-item DB error (asyncpg's poisoned-transaction case), the
    next item in the same batch must still succeed — i.e. no
    InFailedSQLTransactionError cascade.  This is the exact failure mode
    that produced 485 errors in production."""
    seen_values: list[int] = []

    async def process_one(session, item: dict) -> None:
        if item["id"] == 1:
            await session.execute(text("SELECT * FROM no_such_table_t122"))
            return
        # Literal int in SQL — avoids asyncpg's strict bind-type inference
        # for parameterless contexts. The point of this test is the
        # cascade-recovery, not parameter binding.
        row = (await session.execute(
            text(f"SELECT {int(item['id'])}")
        )).scalar_one()
        seen_values.append(row)

    async def run():
        await _process_batch(
            "cascade_test",
            [{"id": 1}, {"id": 2}, {"id": 3}],
            process_one,
            lambda i: i["id"],
        )
        # If the cascade still happened, items 2 and 3 would have raised
        # InFailedSQLTransactionError instead of returning their value.
        assert seen_values == [2, 3]

    _run(run)


# ── _update_last_scanned does not poison the scan loop's session ──────────────

def test_get_db_rolls_back_on_http_exception():
    """``get_db`` is a FastAPI dependency that yields an AsyncSession.
    When the route (downstream of the yield) raises HTTPException —
    which is a normal control-flow signal, not a DB failure — the
    dependency must still rollback the open transaction before the
    session is returned to the pool.  Without this, the next caller
    that picks up the same connection sees InFailedSQLTransactionError.
    """
    async def run():
        gen = get_db()
        session = await gen.__anext__()

        # Mid-route, an UPDATE fails (e.g. the row doesn't exist) and
        # the route maps it to a 4xx HTTPException.
        try:
            await session.execute(text("SELECT * FROM no_such_table_t122_a"))
        except Exception:
            pass

        # Route raises HTTPException — get_db must catch it, rollback,
        # then re-raise it so FastAPI returns the right status code.
        with pytest.raises(HTTPException) as excinfo:
            await gen.athrow(HTTPException(status_code=status.HTTP_404_NOT_FOUND))
        assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND

        # Now reuse a fresh session: if the rollback above worked, the
        # pool gives us a clean connection. If it didn't, this SELECT
        # would raise InFailedSQLTransactionError.
        async with AsyncSessionLocal() as fresh:
            row = (await fresh.execute(text("SELECT 11"))).scalar_one()
            assert row == 11

    _run(run)


def test_get_db_rolls_back_on_generic_exception():
    """When the route raises an unexpected exception (not HTTPException),
    ``get_db`` must rollback the session and translate the error into a
    503 — without leaving a poisoned connection in the pool."""
    async def run():
        gen = get_db()
        session = await gen.__anext__()

        try:
            await session.execute(text("SELECT * FROM no_such_table_t122_b"))
        except Exception:
            pass

        with pytest.raises(HTTPException) as excinfo:
            await gen.athrow(RuntimeError("simulated route failure"))
        assert excinfo.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

        async with AsyncSessionLocal() as fresh:
            row = (await fresh.execute(text("SELECT 13"))).scalar_one()
            assert row == 13

    _run(run)


def test_update_last_scanned_recovers_session_on_failure():
    """If the UPDATE inside _update_last_scanned fails, the session must be
    usable for the next watchlist's queries.  Passing a value with the
    wrong type for the ``id`` column triggers a DB-level error."""
    async def run():
        async with AsyncSessionLocal() as session:
            await _update_last_scanned(
                session, "this-id-is-not-a-valid-uuid-and-row-does-not-exist-122"
            )

            row = (await session.execute(text("SELECT 7"))).scalar_one()
            assert row == 7

    _run(run)
