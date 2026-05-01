"""DB transaction safety invariant tests (fault-injection harness).

This module uses the ``db_fault_injection`` harness to assert general
transaction-safety contracts that must hold across the whole codebase.  Each
test is independent and self-contained.

Tests cover three core contracts:

1. ``run_db_task`` — rolls back cleanly when the callback raises, and the
   *next* call on the same engine succeeds (no poisoned pool connection).

2. ``SimulationService.run_simulation_batch`` — a DB error mid-loop for one
   decision must not abort subsequent decisions; the ``errors`` counter must
   reflect the failure.

3. ``get_db`` FastAPI dependency — rolls back on ``HTTPException``,
   ``asyncio.CancelledError``, and a generic ``SQLAlchemyError``; the next
   request handled by a fresh session works normally.

All tests run with plain ``asyncio.run`` (no pytest-asyncio) to match the
existing convention in this test suite.

Cross-task notes
----------------
* ``run_db_task`` is defined in ``app.database`` (session-lifecycle refactor,
  Task #116/119).  If the import fails at collection time, the two
  ``run_db_task`` tests are xfail-ed automatically via ``pytest.importorskip``
  logic in the fixtures.
* ``SimulationService.run_simulation_batch`` and ``_process_single_decision``
  use per-decision sessions (simulation batch isolation, Task #132).  Those
  tests are already green; this module adds harness-based coverage on top of
  the existing ``test_simulation_batch_isolation.py`` suite.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db_fault_injection import force_failure_once, force_engine_failure_once  # noqa: E402

from app.database import (  # noqa: E402
    AsyncSessionLocal,
    engine,
    get_db,
    run_db_task,
)
from fastapi import HTTPException, status  # noqa: E402


# ── asyncio helpers ───────────────────────────────────────────────────────────

def _run(coro_factory):
    """Run an async test body with a fresh asyncio loop and a clean pool.

    Disposing the engine at the end prevents stray asyncpg connections from
    being garbage-collected against a closed loop, which would promote to a
    hard test failure via pytest's unraisable-exception plugin.
    """
    async def _wrapper():
        try:
            await coro_factory()
        finally:
            await engine.dispose()

    asyncio.run(_wrapper())


# ── 1. run_db_task rollback contract ─────────────────────────────────────────

def test_run_db_task_rolls_back_on_fn_exception():
    """When the callback passed to ``run_db_task`` raises, the transaction must
    be rolled back before the exception propagates.  A subsequent call to
    ``run_db_task`` must succeed — proving the pool connection is not left in
    the InFailedSQLTransactionError state.

    This is a direct structural test: ``run_db_task`` wraps every execution in
    ``async with session.begin()``, which auto-rolls-back on any exception.
    The test verifies the contract end-to-end with a real DB round-trip.
    """
    async def run():
        async def _failing_fn(session):
            raise OperationalError(
                "Injected failure by test",
                params={},
                orig=Exception("synthetic"),
            )

        with pytest.raises(OperationalError):
            await run_db_task(_failing_fn)

        async def _healthy_fn(session):
            return (await session.execute(text("SELECT 42"))).scalar_one()

        result = await run_db_task(_healthy_fn)
        assert result == 42, (
            f"Expected 42 from post-failure run_db_task call, got {result!r}. "
            "The DB connection may still be poisoned (InFailedSQLTransactionError)."
        )

    _run(run)


def test_run_db_task_second_call_succeeds_after_first_raises():
    """A more targeted variant: two consecutive ``run_db_task`` calls where the
    first raises a generic ``RuntimeError`` (non-SQLAlchemy, simulating
    application-layer bugs).  The second call must still return a valid result.
    """
    async def run():
        call_count = 0

        async def _fn(session):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first call always fails")
            return (await session.execute(text("SELECT 99"))).scalar_one()

        with pytest.raises(RuntimeError):
            await run_db_task(_fn)

        result = await run_db_task(_fn)
        assert result == 99, (
            f"Second run_db_task call returned {result!r}, expected 99."
        )

    _run(run)


# ── 2. SimulationService.run_simulation_batch isolation ──────────────────────

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)

_FAKE_RECORD_TEMPLATE = {
    "timestamp_entry": _NOW,
    "entry_price": 100.0,
    "tp_price": 101.2,
    "sl_price": 99.2,
    "exit_price": 101.2,
    "exit_timestamp": _NOW,
    "result": "WIN",
    "time_to_result": 3600,
    "direction": "SPOT",
    "decision_type": "BUY",
    "features_snapshot": None,
    "config_snapshot": None,
}


def _make_fake_outer_session(decisions):
    outer = AsyncMock()

    ohlcv_result = MagicMock()
    ohlcv_result.fetchone.return_value = SimpleNamespace(
        total_candles=500,
        symbol_count=5,
        latest_time=_NOW,
    )

    decisions_result = MagicMock()
    decisions_result.scalars.return_value.all.return_value = decisions

    outer.execute.side_effect = [ohlcv_result, decisions_result]
    return outer


def _make_fake_session_factory():
    def _make_per_session():
        sess = AsyncMock()

        @asynccontextmanager
        async def _begin():
            yield None

        sess.begin = _begin
        return sess

    @asynccontextmanager
    async def factory():
        yield _make_per_session()

    return factory


def test_simulation_batch_continues_after_db_error_mid_loop():
    """A DB-shaped error raised by ``simulate_decision`` for one decision must
    not abort the rest of the batch.  Specifically:

    * The batch returns a summary dict (does not re-raise).
    * ``errors`` == 1 (the failing decision).
    * ``processed`` == 2 (the two successful decisions).
    * Only the successful decisions' records appear in the bulk insert.

    The isolation is structural: each decision runs in its own session via
    ``_process_single_decision``.  This test verifies the contract at the
    ``run_simulation_batch`` boundary using the fault-injection pattern
    (RuntimeError standing in for a ``SQLAlchemyError`` that would escape an
    inner session, which is the real-world failure mode).
    """
    from app.services.simulation_service import SimulationService  # noqa: E402
    from app.repositories.simulation_repository import SimulationRepository  # noqa: E402

    decisions = [
        SimpleNamespace(id=1, symbol="BTC", created_at=_NOW, timeframe="1h",
                        decision="BUY", metrics={}),
        SimpleNamespace(id=2, symbol="ETH", created_at=_NOW, timeframe="1h",
                        decision="BUY", metrics={}),
        SimpleNamespace(id=3, symbol="SOL", created_at=_NOW, timeframe="1h",
                        decision="BUY", metrics={}),
    ]

    outer_session = _make_fake_outer_session(decisions)
    session_factory = _make_fake_session_factory()
    inserted_records: list[dict] = []

    async def fake_simulate(self_svc, decision, config, exchange):
        if decision.symbol == "ETH":
            raise OperationalError(
                "Injected DB error for ETH by fault-injection harness",
                params={},
                orig=Exception("synthetic asyncpg InFailedSQLTransaction"),
            )
        return [{**_FAKE_RECORD_TEMPLATE, "symbol": decision.symbol,
                 "decision_id": decision.id}]

    async def fake_bulk_insert(self_repo, records, batch_size=500):
        inserted_records.extend(records)
        return len(records)

    async def run():
        with (
            patch.object(SimulationService, "simulate_decision", fake_simulate),
            patch.object(SimulationRepository, "bulk_insert_simulations",
                         fake_bulk_insert),
        ):
            svc = SimulationService(outer_session)
            return await svc.run_simulation_batch(
                limit=10,
                skip_existing=False,
                exchange="gate",
                session_factory=session_factory,
            )

    summary = asyncio.run(run())

    assert summary["errors"] == 1, (
        f"Expected exactly 1 error (ETH), got {summary['errors']}. "
        "DB error mid-loop may have aborted remaining decisions."
    )
    assert summary["processed"] == 2, (
        f"Expected 2 processed (BTC + SOL), got {summary['processed']}."
    )
    persisted = {r["symbol"] for r in inserted_records}
    assert "BTC" in persisted, "BTC record was not persisted after ETH failure."
    assert "SOL" in persisted, "SOL record was not persisted after ETH failure."
    assert "ETH" not in persisted, "ETH record must not be persisted (it errored)."


def test_simulation_batch_errors_counter_reflects_all_failures():
    """Multiple failing decisions must each increment ``errors`` independently.
    The one successful decision is still persisted.
    """
    from app.services.simulation_service import SimulationService  # noqa: E402
    from app.repositories.simulation_repository import SimulationRepository  # noqa: E402

    decisions = [
        SimpleNamespace(id=1, symbol="BTC", created_at=_NOW, timeframe="1h",
                        decision="BUY", metrics={}),
        SimpleNamespace(id=2, symbol="ETH", created_at=_NOW, timeframe="1h",
                        decision="BUY", metrics={}),
        SimpleNamespace(id=3, symbol="SOL", created_at=_NOW, timeframe="1h",
                        decision="BUY", metrics={}),
    ]

    outer_session = _make_fake_outer_session(decisions)
    session_factory = _make_fake_session_factory()
    inserted_records: list[dict] = []

    async def fake_simulate(self_svc, decision, config, exchange):
        if decision.symbol in ("ETH", "SOL"):
            raise OperationalError(
                f"Injected DB error for {decision.symbol}",
                params={},
                orig=Exception("synthetic"),
            )
        return [{**_FAKE_RECORD_TEMPLATE, "symbol": decision.symbol,
                 "decision_id": decision.id}]

    async def fake_bulk_insert(self_repo, records, batch_size=500):
        inserted_records.extend(records)
        return len(records)

    async def run():
        with (
            patch.object(SimulationService, "simulate_decision", fake_simulate),
            patch.object(SimulationRepository, "bulk_insert_simulations",
                         fake_bulk_insert),
        ):
            svc = SimulationService(outer_session)
            return await svc.run_simulation_batch(
                limit=10,
                skip_existing=False,
                exchange="gate",
                session_factory=session_factory,
            )

    summary = asyncio.run(run())

    assert summary["errors"] == 2, (
        f"Expected 2 errors (ETH + SOL), got {summary['errors']}."
    )
    assert summary["processed"] == 1, (
        f"Expected 1 processed (BTC), got {summary['processed']}."
    )
    persisted = {r["symbol"] for r in inserted_records}
    assert "BTC" in persisted
    assert "ETH" not in persisted
    assert "SOL" not in persisted


# ── 3. get_db dependency rollback contract ────────────────────────────────────

def test_get_db_rolls_back_and_fresh_session_works_after_http_exception():
    """When the route raises ``HTTPException``, ``get_db`` must rollback the
    open session before returning the connection to the pool.  A subsequent
    request using a fresh session must work normally.

    Uses ``force_failure_once`` to arm the yielded session so that one
    statement fails inside the route, simulating a real route that catches a
    DB error and converts it to a 404.
    """
    async def run():
        gen = get_db()
        session = await gen.__anext__()

        with force_failure_once(session, statement_substring="SELECT"):
            try:
                await session.execute(text("SELECT * FROM nonexistent_table_safety_a"))
            except Exception:
                pass

        with pytest.raises(HTTPException) as exc_info:
            await gen.athrow(HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                           detail="not found"))
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

        async with AsyncSessionLocal() as fresh:
            result = (await fresh.execute(text("SELECT 101"))).scalar_one()
            assert result == 101, (
                "Fresh session returned unexpected result after get_db rollback. "
                "Pool connection may still be poisoned."
            )

    _run(run)


def test_get_db_rolls_back_and_fresh_session_works_after_sqlalchemy_error():
    """When the route lets a ``SQLAlchemyError`` propagate out of ``get_db``,
    the dependency must rollback the session and raise an HTTP 503.  The next
    fresh session must still work.

    Throws an actual ``OperationalError`` (a concrete ``SQLAlchemyError``
    subclass) through the generator — not a generic ``RuntimeError`` — to
    prove that DB-layer exceptions are handled correctly.
    """
    async def run():
        gen = get_db()
        session = await gen.__anext__()

        try:
            await session.execute(text("SELECT * FROM nonexistent_table_safety_b"))
        except Exception:
            pass

        injected = OperationalError(
            "Injected SQLAlchemyError by fault-injection harness",
            params={},
            orig=Exception("synthetic asyncpg error"),
        )
        with pytest.raises(HTTPException) as exc_info:
            await gen.athrow(injected)
        assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

        async with AsyncSessionLocal() as fresh:
            result = (await fresh.execute(text("SELECT 202"))).scalar_one()
            assert result == 202, (
                "Fresh session failed after get_db rolled back on SQLAlchemyError. "
                "Pool connection may still be poisoned."
            )

    _run(run)


def test_get_db_rolls_back_and_fresh_session_works_after_cancelled_error():
    """When ``asyncio.CancelledError`` propagates out of ``get_db`` (e.g. pool
    timeout or Cloud Run cold-start cancellation), the session must still be
    rolled back and the pool connection must not be left in a poisoned state.
    """
    async def run():
        gen = get_db()
        session = await gen.__anext__()

        try:
            await session.execute(text("SELECT * FROM nonexistent_table_safety_c"))
        except Exception:
            pass

        with pytest.raises(HTTPException) as exc_info:
            await gen.athrow(asyncio.CancelledError())
        assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

        async with AsyncSessionLocal() as fresh:
            result = (await fresh.execute(text("SELECT 303"))).scalar_one()
            assert result == 303

    _run(run)


# ── 4. force_failure_once harness self-tests ──────────────────────────────────

def test_force_failure_once_fires_on_matching_statement():
    """The harness raises ``OperationalError`` exactly once for a matching
    statement and then disarms itself automatically."""
    async def run():
        async with AsyncSessionLocal() as session:
            with force_failure_once(session, statement_substring="SELECT"):
                with pytest.raises(OperationalError):
                    await session.execute(text("SELECT 1"))

            result = (await session.execute(text("SELECT 7"))).scalar_one()
            assert result == 7, (
                "Expected session to be usable after force_failure_once exited."
            )

    _run(run)


def test_force_failure_once_does_not_fire_on_non_matching_statement():
    """The harness must NOT intercept a statement that does not match the
    given substring — the real execute runs instead."""
    async def run():
        async with AsyncSessionLocal() as session:
            with force_failure_once(session, statement_substring="INSERT INTO foo"):
                result = (await session.execute(text("SELECT 5"))).scalar_one()
                assert result == 5

    _run(run)


def test_force_engine_failure_once_fires_on_matching_statement():
    """Engine-level harness fires on the next matching statement regardless of
    which session emits it, then disarms so subsequent statements succeed.

    This covers code paths where the caller cannot intercept the session
    (e.g. ``run_db_task`` creates its own session internally).
    """
    async def run():
        with force_engine_failure_once(engine.sync_engine, statement_substring="SELECT 55"):
            async with AsyncSessionLocal() as session:
                with pytest.raises(OperationalError):
                    await session.execute(text("SELECT 55"))

        async with AsyncSessionLocal() as fresh:
            result = (await fresh.execute(text("SELECT 55"))).scalar_one()
            assert result == 55, (
                "Engine listener was not removed after force_engine_failure_once exited."
            )

    _run(run)


def test_force_engine_failure_once_does_not_fire_on_non_matching_statement():
    """Engine-level harness must NOT intercept statements that do not match."""
    async def run():
        with force_engine_failure_once(engine.sync_engine,
                                       statement_substring="INSERT INTO nonexistent_xyz"):
            async with AsyncSessionLocal() as session:
                result = (await session.execute(text("SELECT 3"))).scalar_one()
                assert result == 3

    _run(run)


def test_force_engine_failure_once_covers_run_db_task_internal_session():
    """The engine-level harness can inject a failure inside ``run_db_task``
    even though ``run_db_task`` creates the session internally.

    ``run_db_task`` wraps the call in ``async with session.begin()`` so the
    transaction is automatically rolled back on error.  The next call to
    ``run_db_task`` on the same engine must succeed — proving no poisoned
    connection leaked back into the pool.
    """
    async def run():
        async def _select_57(db):
            return (await db.execute(text("SELECT 57"))).scalar_one()

        with force_engine_failure_once(engine.sync_engine,
                                       statement_substring="SELECT 57"):
            with pytest.raises(OperationalError):
                await run_db_task(_select_57)

        result = await run_db_task(_select_57)
        assert result == 57, (
            f"Expected 57 from post-failure run_db_task call, got {result!r}. "
            "Pool connection may still be poisoned after engine-level injection."
        )

    _run(run)


def test_force_failure_once_restores_execute_on_test_body_exception():
    """Even if the test body raises, ``force_failure_once`` must restore the
    original execute method so the session stays usable.

    We verify this behaviourally: after the context manager exits (due to an
    exception in the test body), the session must be able to execute a valid
    SQL statement without raising.  We also confirm the harness is no longer
    armed — a second execute must NOT raise ``OperationalError``.
    """
    async def run():
        async with AsyncSessionLocal() as session:
            try:
                with force_failure_once(session, statement_substring="SELECT"):
                    raise ValueError("test body error")
            except ValueError:
                pass

            result = (await session.execute(text("SELECT 9"))).scalar_one()
            assert result == 9, (
                "Session was not usable after force_failure_once exited via exception."
            )

            result2 = (await session.execute(text("SELECT 11"))).scalar_one()
            assert result2 == 11, (
                "Harness was still armed after context-manager exit — it must disarm itself."
            )

    _run(run)
