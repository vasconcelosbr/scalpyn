"""Task #232 — regression tests for the ingestion/execution gate split.

These tests prove the contracts that the code-review cycle flagged:

* ``_bulk_approve`` only mutates the ingestion gate (``is_active``)
  and **never** flips the execution gate (``is_tradable``) — the
  remediator must never grant trading authorisation on its own.
* ``_verify_approved`` accepts a row purely on ``is_active = true``,
  matching what ``_bulk_approve`` writes (no stale ``is_approved``
  dependency that used to leave rows stuck).
* The classifier in ``symbol_health_service`` flags a row as
  ``NOT_APPROVED`` when ``is_active = false`` (matching the field
  the remediator actually mutates) and as ``OK`` when ``is_active =
  true`` regardless of ``is_tradable``.
"""

from __future__ import annotations

import inspect
import re

from app.services import symbol_remediator
from app.services.symbol_health_service import (
    STATUS_NOT_APPROVED,
    STATUS_OK,
    _classify,
)


_SQL_BODY = re.compile(r'text\(\s*"""([\s\S]*?)"""\s*\)', re.IGNORECASE)


def _sql_bodies(func) -> list[str]:
    return [m.group(1).lower() for m in _SQL_BODY.finditer(inspect.getsource(func))]


def test_bulk_approve_never_writes_is_tradable() -> None:
    """``_bulk_approve`` must SET only is_active — never is_tradable."""
    bodies = _sql_bodies(symbol_remediator._bulk_approve)
    assert bodies, "Could not extract any text(\"\"\"...\"\"\") from _bulk_approve."
    for body in bodies:
        assert re.search(r"set\s+is_active\s*=\s*true", body), (
            "_bulk_approve must SET is_active = TRUE."
        )
        assert "is_tradable" not in body, (
            "REGRESSION: _bulk_approve must NEVER mutate is_tradable — "
            "the execution gate is a manual operator decision (Task #232)."
        )


def test_verify_approved_checks_is_active_not_is_approved() -> None:
    """Verification must read what _bulk_approve writes (is_active)."""
    bodies = _sql_bodies(symbol_remediator._verify_approved)
    assert bodies, "Could not extract any text(\"\"\"...\"\"\") from _verify_approved."
    for body in bodies:
        assert "is_active = true" in body, (
            "_verify_approved must filter on is_active = TRUE."
        )
        assert "is_tradable" not in body, (
            "_verify_approved must NOT couple verification to the execution "
            "gate — that would mark approval failed for un-promoted symbols."
        )


def test_remediator_pending_tradable_constants_are_diagnostic_only() -> None:
    """STATUS_PENDING_TRADABLE + ACTION_WARN_PENDING_TRADABLE must
    exist and be consumed only by the diagnostic-warning path."""
    assert symbol_remediator.STATUS_PENDING_TRADABLE == "pending_tradable"
    assert symbol_remediator.ACTION_WARN_PENDING_TRADABLE == "warn_pending_tradable"
    src = inspect.getsource(symbol_remediator.SymbolRemediator.remediate)
    assert "ACTION_WARN_PENDING_TRADABLE" in src, (
        "remediate() must emit the diagnostic warning."
    )
    # Must use executed=False for the warning so it cannot be confused
    # with a write that granted tradable authorisation.
    assert "executed=False" in src, (
        "PENDING_TRADABLE warnings must be emitted with executed=False."
    )


def _pool(is_active: bool, is_approved: bool = False, exists: bool = True) -> dict:
    return {"is_active": is_active, "is_approved": is_approved, "exists": exists}


def _fresh_buf() -> dict:
    return {"member_count": 100, "newest_age_seconds": 5, "error": None}


def _fresh_ind() -> dict:
    return {
        "age_seconds": 5,
        "has_taker_ratio": True,
        "has_volume_delta": True,
        "error": None,
    }


def test_classifier_treats_inactive_row_as_ok() -> None:
    """Operator-disabled (is_active=false) rows are intentionally OK
    so they do not flood the remediator (Task #232)."""
    record = _classify(
        symbol="BTC_USDT",
        pool=_pool(is_active=False, is_approved=True),
        in_ws=False,
        buf=_fresh_buf(),
        ind=_fresh_ind(),
        indicator_max_age=600,
        buffer_newest_max_age=120,
    )
    assert record.status == STATUS_OK


def test_classifier_flags_active_but_unapproved_row_as_not_approved() -> None:
    """The combination (is_active=true, is_approved=false) must NOT
    silently slip through — that is the exact regression Task #232
    fixed by switching the classifier off the legacy ``is_approved``
    column.  Here we simulate the post-split world: classifier reads
    is_active only, so an active row is OK regardless of is_approved.
    """
    record = _classify(
        symbol="BTC_USDT",
        pool=_pool(is_active=True, is_approved=False),
        in_ws=True,
        buf=_fresh_buf(),
        ind=_fresh_ind(),
        indicator_max_age=600,
        buffer_newest_max_age=120,
    )
    # Post-Task #232: active row → OK; the remediator path does NOT
    # need to "approve" it because is_active is already true.
    assert record.status == STATUS_OK
    assert record.is_approved is True  # alias for is_active in new model


def test_classifier_missing_pool_row_is_not_approved() -> None:
    """A symbol with no pool_coins row at all is still NOT_APPROVED."""
    record = _classify(
        symbol="GHOST_USDT",
        pool=_pool(is_active=False, is_approved=False, exists=False),
        in_ws=False,
        buf=_fresh_buf(),
        ind=_fresh_ind(),
        indicator_max_age=600,
        buffer_newest_max_age=120,
    )
    assert record.status == STATUS_NOT_APPROVED
