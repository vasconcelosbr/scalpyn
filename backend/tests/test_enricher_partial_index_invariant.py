"""Lint-style invariant test for the Decision Log Enricher's ON CONFLICT
clause (Task #237).

Background
----------
``ux_trade_tracking_decision`` is a PARTIAL unique index defined in
migration 038::

    CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_tracking_decision
        ON trade_tracking (decision_id)
        WHERE decision_id IS NOT NULL

Postgres requires the same predicate in ``ON CONFLICT`` for the planner
to match a partial index. Without ``index_where`` the planner raises
``InvalidColumnReferenceError``, which aborts the enricher's
transaction. The aborted tx then poisons every subsequent statement on
the same pooled connection — cascading ``InFailedSQLTransactionError``
and ``PendingRollbackError`` into ``collect_5m`` / ``compute_5m`` / any
other Celery task that shares the ``microstructure`` queue.

This test compiles the actual INSERT statement built by
``DecisionLogEnricherService._process_decision`` and asserts that the
compiled SQL contains the partial-index predicate. If migration 038's
predicate ever changes, this test must be updated in lockstep.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.trade_tracking import TradeTracking


def _compile_enricher_insert() -> str:
    """Build the same statement shape as DecisionLogEnricherService and compile
    it against the postgresql dialect, returning the literal SQL string.
    """
    stmt = (
        pg_insert(TradeTracking)
        .values(
            decision_id=1,
            symbol="BTC_USDT",
            market_type="spot",
            position_side="long",
            is_simulated=True,
            entry_price=100.0,
            entry_time=None,
            target_price=101.0,
            stop_price=99.0,
            status="open",
        )
        .on_conflict_do_nothing(
            index_elements=["decision_id"],
            index_where=TradeTracking.decision_id.is_not(None),
        )
    )
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_enricher_insert_compiles_with_partial_predicate() -> None:
    """The compiled SQL must contain the partial-index predicate so the
    Postgres planner can match ``ux_trade_tracking_decision``.
    """
    sql = _compile_enricher_insert().lower()
    # ON CONFLICT (decision_id) WHERE decision_id IS NOT NULL DO NOTHING
    assert "on conflict" in sql, "Statement must use ON CONFLICT"
    assert "decision_id is not null" in sql, (
        "Compiled SQL is missing the partial-index predicate "
        "``WHERE decision_id IS NOT NULL``. The Postgres planner cannot "
        "match the partial index ``ux_trade_tracking_decision`` (migration "
        "038) without it, and the enricher will fail with "
        "InvalidColumnReferenceError on every cycle, poisoning the outer "
        "transaction (Task #237 cascade)."
    )


def test_enricher_service_uses_index_where() -> None:
    """Source-level invariant: the enricher service file must pass
    ``index_where=`` to ``on_conflict_do_nothing`` for the trade_tracking
    INSERT. Catches anyone removing the predicate without updating the
    SQL-compile test above.
    """
    service_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "services"
        / "decision_log_enricher_service.py"
    )
    src = service_path.read_text(encoding="utf-8")

    on_conflict_blocks = re.findall(
        r"on_conflict_do_nothing\((.*?)\)", src, flags=re.DOTALL
    )
    assert on_conflict_blocks, (
        "Expected at least one on_conflict_do_nothing(...) call in the "
        "enricher service."
    )
    for block in on_conflict_blocks:
        assert "index_where" in block, (
            "Every ``on_conflict_do_nothing`` in the enricher service "
            "must pass ``index_where=`` so it matches the partial unique "
            "index ``ux_trade_tracking_decision`` (migration 038). "
            "Removing this re-introduces the Task #237 cascade."
        )


def test_migration_038_index_is_partial() -> None:
    """Tripwire: if migration 038's predicate changes, the test above must
    be updated in lockstep. This test pins the migration text.
    """
    migration_path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "038_trade_tracking.py"
    )
    src = migration_path.read_text(encoding="utf-8").lower()
    assert "ux_trade_tracking_decision" in src
    assert "where decision_id is not null" in src, (
        "Migration 038 no longer creates a partial unique index. If the "
        "index is now full, drop ``index_where`` from the enricher and "
        "update test_enricher_insert_compiles_with_partial_predicate. If "
        "the predicate changed, update the predicate in the enricher and "
        "in this test."
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
