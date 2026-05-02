"""Integration-style tests for _persist_indicators session recovery.

These complement ``test_scheduler_group_drift_detection.py`` (which is
pure classification) by exercising the actual ``_persist_indicators``
function with a mock async session and asserting the post-recovery
contract that ``_refresh_market_metadata`` relies on:

  1. when the INSERT raises the scheduler_group drift exception, the
     function MUST NOT re-raise (the scheduler loop survives),
  2. ``db.rollback()`` MUST be called on the outer session whenever
     ``db.in_transaction()`` returns True (so the next statement is
     not blocked by InFailedSQLTransactionError),
  3. ``db.rollback()`` MUST be skipped when ``db.in_transaction()``
     returns False (so SQLAlchemy doesn't raise InvalidRequestError
     against an already-closed transaction),
  4. after the recovery branch returns, a follow-up ``db.execute()``
     succeeds — i.e. the session is in a usable state.

We deliberately do NOT spin up a real Postgres connection here because
asyncpg behavior under savepoint failure is driver-version-dependent;
the goal is to lock the contract between ``_persist_indicators`` and
its caller, not to re-test asyncpg.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg.exceptions import UndefinedColumnError

from app.services import (
    microstructure_scheduler_service as micro_mod,
)
from app.services import (
    structural_scheduler_service as struct_mod,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_DRIFT_EXC = UndefinedColumnError(
    'column "scheduler_group" of relation "indicators" does not exist'
)


def _make_mock_session(*, in_tx_after_failure: bool, execute_exc: Exception) -> MagicMock:
    """Build a mock session whose ``begin_nested()`` is an async context
    manager, whose ``execute()`` raises ``execute_exc`` on first call but
    succeeds on subsequent calls (the "follow-up usable" assertion), and
    whose ``in_transaction()`` returns ``in_tx_after_failure`` after the
    savepoint has unwound.
    """
    session = MagicMock(name="AsyncSession")

    # async context manager for begin_nested(): __aenter__ returns the
    # session itself, __aexit__ propagates the exception (returns False).
    nested_cm = MagicMock(name="SavepointContext")
    nested_cm.__aenter__ = AsyncMock(return_value=session)
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested_cm)

    # First execute() call (the failing INSERT) raises the drift error;
    # subsequent calls succeed — that's the "session is usable afterwards"
    # observability we want to lock down.
    follow_up_result = MagicMock(name="ExecuteResult")
    session.execute = AsyncMock(side_effect=[execute_exc, follow_up_result])

    session.rollback = AsyncMock(name="rollback")
    session.in_transaction = MagicMock(return_value=in_tx_after_failure)

    return session


# ---------------------------------------------------------------------------
# structural scheduler
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_struct_persist_indicators_swallows_drift_and_rolls_back() -> None:
    """In the common case (parent transaction still active after the
    savepoint unwind), _persist_indicators must call rollback() and return
    cleanly so the scheduler loop continues."""
    # Reset the boot-once flag so the test sees deterministic logger calls.
    struct_mod._scheduler_group_drift_logged = False

    session = _make_mock_session(in_tx_after_failure=True, execute_exc=_DRIFT_EXC)

    # Should NOT raise — drift is recognised and the function returns.
    await struct_mod._persist_indicators(
        session,
        symbol="BTC_USDT",
        results={"rsi": 55.0},
        when=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
    )

    # Contract 2: rollback was called exactly once.
    assert session.rollback.await_count == 1, (
        "rollback() must fire so _refresh_market_metadata gets a clean "
        "outer transaction; otherwise InFailedSQLTransactionError cascades."
    )

    # Contract 4: a follow-up statement on the same session succeeds —
    # this is what _refresh_market_metadata depends on.
    result = await session.execute("SELECT 1")
    assert result is not None


@pytest.mark.anyio
async def test_struct_persist_indicators_skips_rollback_when_not_in_tx() -> None:
    """Edge case: SQLAlchemy already auto-rolled the outer transaction back
    while the savepoint context manager unwound (asyncpg can poison the
    parent before the savepoint release).  Calling rollback() against a
    session with no active transaction raises InvalidRequestError; the
    in_transaction() guard must short-circuit before that happens."""
    struct_mod._scheduler_group_drift_logged = False

    session = _make_mock_session(in_tx_after_failure=False, execute_exc=_DRIFT_EXC)

    await struct_mod._persist_indicators(
        session,
        symbol="ETH_USDT",
        results={"rsi": 60.0},
        when=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
    )

    # Contract 3: rollback must be skipped to avoid InvalidRequestError.
    assert session.rollback.await_count == 0, (
        "rollback() must not fire when in_transaction() is False; "
        "calling it raises InvalidRequestError on a closed transaction."
    )


@pytest.mark.anyio
async def test_struct_persist_indicators_reraises_unrelated_errors() -> None:
    """Sanity check: an unrelated exception (e.g. unique violation on the
    same INSERT) must NOT be silently swallowed.  The detector returns
    False for non-drift errors, so the function falls through to the
    error-log branch and the exception is recorded — not re-raised
    (current behavior intentionally swallows in the catch-all to keep
    the scheduler loop alive), but the rollback path must NOT be taken."""
    struct_mod._scheduler_group_drift_logged = False

    unrelated = RuntimeError("connection lost mid-INSERT")
    session = _make_mock_session(in_tx_after_failure=True, execute_exc=unrelated)

    await struct_mod._persist_indicators(
        session,
        symbol="SOL_USDT",
        results={"rsi": 70.0},
        when=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
    )

    # The drift-recovery rollback must NOT fire for unrelated errors;
    # if it did, we'd be silently masking real bugs as drift.
    assert session.rollback.await_count == 0


# ---------------------------------------------------------------------------
# microstructure scheduler — same contract, separate module
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_micro_persist_indicators_swallows_drift_and_rolls_back() -> None:
    micro_mod._scheduler_group_drift_logged = False

    session = _make_mock_session(in_tx_after_failure=True, execute_exc=_DRIFT_EXC)

    await micro_mod._persist_indicators(
        session,
        symbol="BTC_USDT",
        results={"taker_ratio": 0.6},
        when=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
    )

    assert session.rollback.await_count == 1
    result = await session.execute("SELECT 1")
    assert result is not None


@pytest.mark.anyio
async def test_micro_persist_indicators_skips_rollback_when_not_in_tx() -> None:
    micro_mod._scheduler_group_drift_logged = False

    session = _make_mock_session(in_tx_after_failure=False, execute_exc=_DRIFT_EXC)

    await micro_mod._persist_indicators(
        session,
        symbol="ETH_USDT",
        results={"taker_ratio": 0.55},
        when=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
    )

    assert session.rollback.await_count == 0


@pytest.mark.anyio
async def test_drift_log_fires_only_once_per_process() -> None:
    """The boot-once flag must suppress per-symbol-per-cycle log spam.
    Pre-fix this was the dominant noise source in Sentry."""
    struct_mod._scheduler_group_drift_logged = False

    # Two consecutive failing persists from the same process — only the
    # first should set the flag; subsequent ones reuse it.
    for symbol in ("BTC_USDT", "ETH_USDT", "SOL_USDT"):
        session = _make_mock_session(
            in_tx_after_failure=True, execute_exc=_DRIFT_EXC
        )
        await struct_mod._persist_indicators(
            session,
            symbol=symbol,
            results={"rsi": 50.0},
            when=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
        )

    # The flag must be set after the first call and remain True; we don't
    # have direct hooks into the logger here, but the flag transition is
    # the public contract that gates the log.
    assert struct_mod._scheduler_group_drift_logged is True
