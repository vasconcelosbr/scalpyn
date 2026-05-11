"""Task #274 — robust event-loop teardown for the Celery ``_run_async`` helper.

These tests lock in the contract that the finally block in
``backend.app.tasks.collect_market_data._run_async`` NEVER lets a teardown
traceback (e.g. ``RuntimeError: Event loop is closed``) reach the root logger,
even when the inner coroutine raises mid-flight.

Background — the original failure mode (May/2026, ``scalpyn-worker-structural``):
a deadlock poisoned the outer transaction, ``_inner`` raised
``PendingRollbackError``, the asyncpg connection was GC'd AFTER ``loop.close()``,
``__del__`` fired ``_cancel_current_command`` → ``loop.create_task(...)`` on
the closed loop → ``RuntimeError: Event loop is closed`` (23 in 30 min).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tasks.collect_market_data import _run_async  # noqa: E402


class _RecordingHandler(logging.Handler):
    """Captures log records emitted at any level so the test can assert
    that no WARNING/ERROR/EXCEPTION mentioning 'Event loop is closed'
    escapes the teardown path."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _attach_root_recorder() -> _RecordingHandler:
    handler = _RecordingHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    return handler


def _detach(handler: _RecordingHandler) -> None:
    logging.getLogger().removeHandler(handler)


def _has_teardown_noise(records: list[logging.LogRecord]) -> bool:
    """Return True if any WARNING+ record mentions the canonical teardown
    failure modes we are guarding against."""
    needles = ("Event loop is closed", "attached to a different loop")
    for r in records:
        if r.levelno < logging.WARNING:
            continue
        msg = r.getMessage()
        if any(n in msg for n in needles):
            return True
    return False


# ── 1. Successful coroutine returns its value cleanly. ───────────────────────

def test_run_async_returns_value_on_success():
    handler = _attach_root_recorder()
    try:
        from app import database as db_module

        class _FakePool:
            _all_conns: list = []

        class _FakeSyncEngine:
            pool = _FakePool()

        class _FakeEngine:
            sync_engine = _FakeSyncEngine()

            async def dispose(self):
                return None

        async def _ok():
            return 42

        with patch.object(db_module, "_celery_engine", _FakeEngine()):
            result = _run_async(_ok())

        assert result == 42
        assert not _has_teardown_noise(handler.records)
    finally:
        _detach(handler)


# ── 2. Coroutine raises → exception propagates, teardown stays silent. ──────

def test_run_async_propagates_exception_without_teardown_noise():
    handler = _attach_root_recorder()
    try:
        class _BoomError(RuntimeError):
            pass

        async def _boom():
            raise _BoomError("simulated PendingRollbackError")

        with pytest.raises(_BoomError):
            _run_async(_boom())

        assert not _has_teardown_noise(handler.records)
    finally:
        _detach(handler)


# ── 3. dispose() itself raises 'Event loop is closed' — must be swallowed. ──

def test_run_async_swallows_dispose_event_loop_closed_error():
    """Simulates the exact failure: ``_celery_engine.dispose()`` raises
    ``RuntimeError: Event loop is closed`` because asyncpg tried to schedule
    a coroutine on a loop that's already shutting down. The teardown must
    log at DEBUG only and never propagate."""
    handler = _attach_root_recorder()
    try:
        from app import database as db_module

        class _FakePool:
            _all_conns: list = []

        class _FakeSyncEngine:
            pool = _FakePool()

        class _FakeEngine:
            sync_engine = _FakeSyncEngine()

            async def dispose(self):
                raise RuntimeError("Event loop is closed")

        async def _ok():
            return "done"

        with patch.object(db_module, "_celery_engine", _FakeEngine()):
            result = _run_async(_ok())

        assert result == "done"
        assert not _has_teardown_noise(handler.records)
    finally:
        _detach(handler)


# ── 4. Pending asyncio task left behind by inner coro is cancelled cleanly. ─

def test_run_async_cancels_orphaned_inner_tasks():
    """If the inner coro spawns a long-running background task and exits
    without awaiting it, the teardown must cancel it without raising and
    without emitting an 'Event loop is closed' warning when that task
    eventually settles."""
    handler = _attach_root_recorder()
    try:
        from app import database as db_module

        class _FakePool:
            _all_conns: list = []

        class _FakeSyncEngine:
            pool = _FakePool()

        class _FakeEngine:
            sync_engine = _FakeSyncEngine()

            async def dispose(self):
                return None

        async def _spawn_and_leave():
            async def _bg():
                await asyncio.sleep(60)

            asyncio.create_task(_bg())
            return "left"

        with patch.object(db_module, "_celery_engine", _FakeEngine()):
            result = _run_async(_spawn_and_leave())

        assert result == "left"
        assert not _has_teardown_noise(handler.records)
    finally:
        _detach(handler)


# ── 5. Hard-terminate sweep handles asyncpg-shaped fakes safely. ────────────

def test_run_async_hard_terminates_pool_connections():
    """The Step-3 sweep must call ``terminate()`` on each asyncpg connection
    still cached in the engine pool, and must tolerate any shape variation
    across SQLAlchemy/asyncpg versions without raising."""
    handler = _attach_root_recorder()
    try:
        from app import database as db_module

        terminate_calls: list[str] = []

        class _FakeAsyncpgConn:
            def __init__(self, name):
                self._name = name

            def terminate(self):
                terminate_calls.append(self._name)

        class _FakeAdaptedConn:
            def __init__(self, name):
                self._connection = _FakeAsyncpgConn(name)

        class _FakeRecord:
            def __init__(self, name):
                self.dbapi_connection = _FakeAdaptedConn(name)

        class _FakePool:
            _all_conns = [_FakeRecord("c1"), _FakeRecord("c2")]

        class _FakeSyncEngine:
            pool = _FakePool()

        class _FakeEngine:
            sync_engine = _FakeSyncEngine()

            async def dispose(self):
                return None

        async def _ok():
            return None

        with patch.object(db_module, "_celery_engine", _FakeEngine()):
            _run_async(_ok())

        assert sorted(terminate_calls) == ["c1", "c2"]
        assert not _has_teardown_noise(handler.records)
    finally:
        _detach(handler)
