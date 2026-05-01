"""Regression test for Task #132 — simulation batch transaction isolation.

Verifies that a RuntimeError raised by ``SimulationService.simulate_decision``
for one decision inside ``run_simulation_batch`` does **not** abort the rest of
the batch.  Specifically:

* The batch completes and returns a summary dict.
* Decisions that do not raise are processed and their records are persisted.
* The ``errors`` counter in the summary correctly reflects the failing decision.
* The ``processed`` counter only counts decisions that did not error.

The isolation is guaranteed structurally: each decision runs inside
``_process_single_decision``, which opens its own session/transaction via
the ``session_factory`` parameter.  A failure in one session cannot poison
another.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.simulation_service import SimulationService  # noqa: E402
from app.repositories.simulation_repository import SimulationRepository  # noqa: E402

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


def _make_decisions():
    """Three fake decisions: BTC, ETH (will fail), SOL."""
    return [
        SimpleNamespace(
            id=1, symbol="BTC", created_at=_NOW, timeframe="1h",
            decision="BUY", metrics={},
        ),
        SimpleNamespace(
            id=2, symbol="ETH", created_at=_NOW, timeframe="1h",
            decision="BUY", metrics={},
        ),
        SimpleNamespace(
            id=3, symbol="SOL", created_at=_NOW, timeframe="1h",
            decision="BUY", metrics={},
        ),
    ]


def _make_outer_session(decisions):
    """AsyncMock outer session handling OHLCV check + decisions fetch."""
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


def _make_session_factory():
    """Returns a factory (async-context-manager function) yielding fresh session
    mocks.  Each session supports ``async with session.begin():``."""

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


def test_single_decision_error_does_not_abort_batch():
    """Injecting RuntimeError in simulate_decision for ETH must not prevent
    BTC and SOL from being processed; errors counter must be 1."""

    decisions = _make_decisions()
    outer_session = _make_outer_session(decisions)
    session_factory = _make_session_factory()

    inserted_records: list[dict] = []

    async def fake_simulate(self_svc, decision, config, exchange):
        if decision.symbol == "ETH":
            raise RuntimeError("Injected DB error for ETH — should be isolated")
        return [{**_FAKE_RECORD_TEMPLATE, "symbol": decision.symbol, "decision_id": decision.id}]

    async def fake_bulk_insert(self_repo, records, batch_size=500):
        inserted_records.extend(records)
        return len(records)

    async def run():
        with (
            patch.object(SimulationService, "simulate_decision", fake_simulate),
            patch.object(SimulationRepository, "bulk_insert_simulations", fake_bulk_insert),
        ):
            svc = SimulationService(outer_session)
            summary = await svc.run_simulation_batch(
                limit=10,
                skip_existing=False,
                exchange="gate",
                session_factory=session_factory,
            )
        return summary

    summary = asyncio.run(run())

    assert summary["errors"] == 1, (
        f"Expected exactly 1 error (ETH), got {summary['errors']}"
    )
    assert summary["processed"] == 2, (
        f"Expected 2 processed decisions (BTC + SOL), got {summary['processed']}"
    )
    assert summary["simulated"] == 2, (
        f"Expected 2 simulation records (BTC + SOL), got {summary['simulated']}"
    )

    persisted_symbols = {r["symbol"] for r in inserted_records}
    assert "BTC" in persisted_symbols, "BTC record was not persisted"
    assert "SOL" in persisted_symbols, "SOL record was not persisted"
    assert "ETH" not in persisted_symbols, "ETH record should not be persisted"


def test_batch_errors_accumulate_correctly():
    """Two failing decisions must result in errors==2 and the one successful
    decision still persisted."""

    decisions = _make_decisions()
    outer_session = _make_outer_session(decisions)
    session_factory = _make_session_factory()

    inserted_records: list[dict] = []

    async def fake_simulate(self_svc, decision, config, exchange):
        if decision.symbol in ("ETH", "SOL"):
            raise RuntimeError(f"Injected error for {decision.symbol}")
        return [{**_FAKE_RECORD_TEMPLATE, "symbol": decision.symbol, "decision_id": decision.id}]

    async def fake_bulk_insert(self_repo, records, batch_size=500):
        inserted_records.extend(records)
        return len(records)

    async def run():
        with (
            patch.object(SimulationService, "simulate_decision", fake_simulate),
            patch.object(SimulationRepository, "bulk_insert_simulations", fake_bulk_insert),
        ):
            svc = SimulationService(outer_session)
            return await svc.run_simulation_batch(
                limit=10,
                skip_existing=False,
                exchange="gate",
                session_factory=session_factory,
            )

    summary = asyncio.run(run())

    assert summary["errors"] == 2, f"Expected 2 errors, got {summary['errors']}"
    assert summary["processed"] == 1, f"Expected 1 processed, got {summary['processed']}"
    persisted_symbols = {r["symbol"] for r in inserted_records}
    assert "BTC" in persisted_symbols
    assert "ETH" not in persisted_symbols
    assert "SOL" not in persisted_symbols


def test_ohlcv_preflight_still_aborts_whole_batch():
    """The OHLCV preflight check must still raise RuntimeError when candle
    data is absent, aborting the whole batch before any decision is touched."""

    outer_session = AsyncMock()

    empty_ohlcv = MagicMock()
    empty_ohlcv.fetchone.return_value = SimpleNamespace(
        total_candles=0, symbol_count=0, latest_time=None
    )
    outer_session.execute.return_value = empty_ohlcv

    processed_decisions: list = []

    async def fake_simulate(self_svc, decision, config, exchange):
        processed_decisions.append(decision.symbol)
        return []

    async def run():
        with patch.object(SimulationService, "simulate_decision", fake_simulate):
            svc = SimulationService(outer_session)
            try:
                await svc.run_simulation_batch(
                    limit=10,
                    skip_existing=False,
                    exchange="gate",
                    session_factory=_make_session_factory(),
                )
                return False
            except RuntimeError:
                return True

    raised = asyncio.run(run())
    assert raised, "Expected RuntimeError from OHLCV preflight to propagate"
    assert not processed_decisions, "No decisions should be processed when OHLCV fails"
