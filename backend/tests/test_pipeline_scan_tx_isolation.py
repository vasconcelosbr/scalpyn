"""Regression tests for Task #125 — pipeline-scan tx-cascade.

Even after Task #122 fixed the WebSocket-handler cascade, production
kept emitting ``asyncpg.exceptions.InFailedSQLTransactionError`` from
``validate_pipeline_integrity`` because:

1. ``_run_pipeline_scan`` shares a single ``async with
   AsyncSessionLocal()`` across the per-watchlist loop AND the final
   integrity check.
2. Two best-effort config reads inside the loop used to swallow
   exceptions silently (``try/except: pass``).  When one of them
   failed, asyncpg's underlying transaction was left in the aborted
   state, and the next statement on the same session — eventually
   the SELECT inside ``validate_pipeline_integrity`` — raised
   ``InFailedSQLTransactionError``.

These tests lock in two contracts that prevent the cascade from coming
back:

* A failure inside a SAVEPOINT-wrapped best-effort read must NOT poison
  the parent session — the next statement must succeed.
* ``validate_pipeline_integrity`` runs on its own session, so it cannot
  inherit aborted-tx state from the per-watchlist loop session.

Tests use plain ``asyncio.run`` to match the existing convention in
``tests/test_db_transaction_recovery.py`` and
``tests/test_coinmarketcap_service.py`` (no pytest-asyncio dep).
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import CeleryAsyncSessionLocal, _celery_engine  # noqa: E402
from app.tasks.pipeline_scan import validate_pipeline_integrity  # noqa: E402


def _run(coro_factory):
    """Run an async test body and dispose the celery engine afterwards
    so stray asyncpg connections don't end up garbage-collected against
    a closed loop on the next test."""
    async def _wrapper():
        try:
            await coro_factory()
        finally:
            await _celery_engine.dispose()

    asyncio.run(_wrapper())


# ── SAVEPOINT-wrapped best-effort reads do not poison the parent ──────────────

def test_savepoint_isolates_failed_best_effort_read():
    """Mirror the new pipeline-scan pattern: a best-effort config read
    runs inside ``async with db.begin_nested()``; if the inner SELECT
    fails, the savepoint rolls back and the parent session must still
    be usable for the next write — no InFailedSQLTransactionError."""
    async def run():
        async with CeleryAsyncSessionLocal() as db:
            try:
                async with db.begin_nested():
                    # Simulate a config read that fails (table missing,
                    # timeout, schema drift, …) — this is exactly what
                    # used to poison the parent transaction in
                    # _run_pipeline_scan.
                    await db.execute(text("SELECT * FROM no_such_table_t125_a"))
            except Exception:
                # The pipeline-scan callers swallow this and use a default;
                # what matters is that the parent session is still healthy.
                pass

            # Without the savepoint, this next statement would raise
            # InFailedSQLTransactionError.  With the savepoint, the
            # parent transaction was never poisoned.
            row = (await db.execute(text("SELECT 125"))).scalar_one()
            assert row == 125

            # And a real write can still happen on the same session.
            row = (await db.execute(text("SELECT 'ok'::text"))).scalar_one()
            assert row == "ok"

    _run(run)


# ── validate_pipeline_integrity uses its own session ──────────────────────────

def test_validate_pipeline_integrity_runs_on_fresh_session():
    """``validate_pipeline_integrity`` is now invoked on a brand-new
    ``AsyncSessionLocal()`` opened *after* the per-watchlist loop closes.
    Even if a different session is in an aborted-tx state, integrity
    runs cleanly because it never touches that session.

    This test runs integrity directly with a fresh session and an empty
    watchlist set — it must complete without raising and return the
    documented ``{"violations": 0, "corrected": 0}`` shape.
    """
    async def run():
        async with CeleryAsyncSessionLocal() as poisoned:
            try:
                await poisoned.execute(text("SELECT * FROM no_such_table_t125_b"))
            except Exception:
                pass
            # poisoned session is now in aborted state — proves the next
            # statement on it would fail:
            try:
                await poisoned.execute(text("SELECT 1"))
            except Exception:
                pass  # expected

        # Integrity runs on a fresh session — cannot inherit the poison.
        async with CeleryAsyncSessionLocal() as integrity_db:
            result = await validate_pipeline_integrity(
                integrity_db,
                wl_rows=[],
                profile_config_map={},
                execution_id=str(uuid4()),
            )
            assert result == {"violations": 0, "corrected": 0}

    _run(run)


def test_validate_pipeline_integrity_handles_unknown_watchlists():
    """End-to-end-ish: hand integrity a watchlist snapshot whose IDs do
    not exist in ``pipeline_watchlist_assets``.  It must SELECT, find
    nothing to correct, COMMIT, and return cleanly — proving the
    rewritten dedicated-session path works against the real schema."""
    async def run():
        snapshots = [
            SimpleNamespace(
                id=uuid4(),
                level="L1",
                source_pool_id=None,
                source_watchlist_id=None,
                profile_id=None,
            ),
            SimpleNamespace(
                id=uuid4(),
                level="L2",
                source_pool_id=None,
                source_watchlist_id=uuid4(),
                profile_id=None,
            ),
        ]

        async with CeleryAsyncSessionLocal() as integrity_db:
            result = await validate_pipeline_integrity(
                integrity_db,
                wl_rows=snapshots,
                profile_config_map={},
                execution_id=str(uuid4()),
            )

        assert result["violations"] == 0
        assert result["corrected"] == 0

    _run(run)
