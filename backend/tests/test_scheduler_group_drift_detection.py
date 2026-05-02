"""Lock the scheduler_group drift detector against SQLAlchemy wrapping
variations.

Regression coverage for Task #178. The detector lives in both scheduler
modules and is the trigger that:
  1. emits the boot-once SCHEMA DRIFT log line,
  2. drains the failed parent transaction with `db.rollback()`,
  3. silently returns instead of letting the per-symbol error storm
     flood Sentry.

A false-NEGATIVE (drift not detected) reintroduces the ~30k errors/day
production cascade. A false-POSITIVE (unrelated asyncpg error swallowed)
silently skips legitimately-broken inserts. Both directions matter, so
this file checks both — and pins the behavior across the two shapes
SQLAlchemy can present the asyncpg failure in:

  * raw `asyncpg.exceptions.UndefinedColumnError`
    (could happen if a future code path used asyncpg directly)
  * `sqlalchemy.exc.ProgrammingError` whose `.orig` is the asyncpg
    exception (the shape SQLAlchemy 2.0 emits for asyncpg-driven
    sessions; this is what production actually sees)

Critical false-positive case: an UNRELATED asyncpg failure on the same
INSERT INTO indicators (scheduler_group, …) statement (e.g. lock
timeout, unique violation, FK violation) MUST NOT be classified as
drift, because that would silently skip a legitimately-broken insert.
The detector therefore requires the column name to appear in the
asyncpg exception's OWN message — never in the str() of the SQLAlchemy
wrapper, which always echoes the SQL statement and would false-match.
"""

from __future__ import annotations

from asyncpg.exceptions import UndefinedColumnError
from sqlalchemy.exc import ProgrammingError

from app.services.microstructure_scheduler_service import (
    _is_scheduler_group_drift as micro_is_drift,
)
from app.services.structural_scheduler_service import (
    _is_scheduler_group_drift as struct_is_drift,
)


_DRIFT_MSG = 'column "scheduler_group" of relation "indicators" does not exist'


def _wrap_in_sa(orig: BaseException) -> ProgrammingError:
    """Construct a ProgrammingError that carries `orig` the way SQLAlchemy
    does in production (positional args + .orig set explicitly)."""
    return ProgrammingError(
        statement="INSERT INTO indicators (scheduler_group) VALUES (...)",
        params=None,
        orig=orig,
    )


# --- TRUE positives: every shape of the scheduler_group drift -------------


def test_raw_asyncpg_undefined_column_with_scheduler_group_is_drift() -> None:
    exc = UndefinedColumnError(_DRIFT_MSG)
    assert struct_is_drift(exc) is True
    assert micro_is_drift(exc) is True


def test_sa_programming_error_wrapping_asyncpg_is_drift() -> None:
    """The shape SQLAlchemy actually emits in production for asyncpg."""
    wrapped = _wrap_in_sa(UndefinedColumnError(_DRIFT_MSG))
    assert struct_is_drift(wrapped) is True
    assert micro_is_drift(wrapped) is True


# --- TRUE negatives: false-positive guards --------------------------------


def test_unrelated_undefined_column_is_not_drift() -> None:
    """A missing column on a totally different table must NOT enter the
    silent-skip path — that would mask legitimately-broken inserts."""
    exc = UndefinedColumnError('column "foo" of relation "bar" does not exist')
    assert struct_is_drift(exc) is False
    assert micro_is_drift(exc) is False


def test_sa_programming_error_unrelated_column_is_not_drift() -> None:
    """The wrapper's str() includes the failing SQL — which always mentions
    `scheduler_group` because that IS the INSERT column.  Substring-matching
    str(wrapped) would mis-classify this as drift; the detector must look
    at .orig's message instead."""
    wrapped = _wrap_in_sa(
        UndefinedColumnError('column "foo" of relation "bar" does not exist')
    )
    assert struct_is_drift(wrapped) is False
    assert micro_is_drift(wrapped) is False


def test_sa_programming_error_unique_violation_on_indicators_is_not_drift() -> None:
    """Realistic false-positive case: a unique-violation error against the
    same INSERT INTO indicators (scheduler_group, …) statement.  The SQL
    statement contains "scheduler_group", but the underlying asyncpg error
    is not an UndefinedColumnError — so the detector must NOT silence it.
    """
    wrapped = _wrap_in_sa(
        Exception(
            'duplicate key value violates unique constraint "ix_indicators_time_symbol_timeframe"'
        )
    )
    assert struct_is_drift(wrapped) is False
    assert micro_is_drift(wrapped) is False


def test_random_runtime_error_is_not_drift() -> None:
    assert struct_is_drift(RuntimeError("connection lost")) is False
    assert micro_is_drift(ValueError("payload invalid")) is False


def test_message_mentioning_column_in_unrelated_context_is_not_drift() -> None:
    """A vanilla Exception that happens to mention the column name in an
    unrelated message (e.g. a log helper) must NOT be treated as drift —
    the detector requires the asyncpg UndefinedColumnError type, not just
    substring match on any exception."""
    exc = RuntimeError("computed scheduler_group hash for batch")
    assert struct_is_drift(exc) is False
    assert micro_is_drift(exc) is False
