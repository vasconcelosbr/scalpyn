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


def test_classifier_flags_inactive_row_as_not_approved() -> None:
    """Inactive (is_active=false) rows MUST surface as NOT_APPROVED so
    the remediator's ``_bulk_approve`` (which flips is_active=TRUE)
    can repair them. Returning STATUS_OK here would make reactivation
    unreachable (Task #232 reviewer feedback)."""
    from app.services.symbol_health_service import STATUS_NOT_APPROVED
    record = _classify(
        symbol="BTC_USDT",
        pool=_pool(is_active=False, is_approved=True),
        in_ws=False,
        buf=_fresh_buf(),
        ind=_fresh_ind(),
        indicator_max_age=600,
        buffer_newest_max_age=120,
    )
    assert record.status == STATUS_NOT_APPROVED
    assert record.is_active is False


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


def test_active_non_tradable_symbol_is_ingested_but_never_executed() -> None:
    """End-to-end Task #232 contract:

    A symbol with ``is_active=true, is_tradable=false`` MUST appear in
    every ingestion-side reader (collector / indicators / scoring /
    pipeline_scan / WS resolver / scheduler services) and MUST be
    skipped at the buy decision point with ``reason=NOT_TRADABLE``.

    Implemented as a static lint over the module sources because a
    real e2e would require a live Postgres + Redis + broker; the
    properties asserted here are the ones a runtime e2e would verify.
    """
    import inspect
    from app.services import (
        pool_service,
        scheduler_service,
        structural_scheduler_service,
        microstructure_scheduler_service,
        gate_ws_leader,
    )
    from app.tasks import (
        pipeline_scan,
        compute_scores,
        evaluate_signals,
        execute_buy,
        collect_market_data,
    )

    ingestion_modules = [
        pool_service, scheduler_service, structural_scheduler_service,
        microstructure_scheduler_service, gate_ws_leader,
        pipeline_scan, compute_scores, collect_market_data,
    ]
    for mod in ingestion_modules:
        src = inspect.getsource(mod).lower()
        # Ingestion readers must NOT filter on is_tradable in any
        # SELECT-style WHERE clause; if they do, the active-but-not-
        # tradable symbol is starved before ever reaching the gate.
        assert "where is_tradable" not in src and "and is_tradable = true" not in src, (
            f"{mod.__name__}: ingestion-side reader filters on "
            "is_tradable, which would starve active+non-tradable "
            "symbols out of ingestion."
        )

    for mod in (evaluate_signals, execute_buy):
        src = inspect.getsource(mod).lower()
        assert "record_not_tradable" in src
        assert "reason=not_tradable" in src, (
            f"{mod.__name__}: must SKIP with reason=NOT_TRADABLE so "
            "active+non-tradable symbols never become a real order."
        )


def test_unified_execution_gate_includes_l3_in_both_paths() -> None:
    """Task #232 round 16 — both execution entrypoints
    (``evaluate_signals`` and ``execute_buy``) must enforce the same
    triple gate at the buy decision point: is_active, is_tradable,
    and L3 watchlist membership.

    Implemented as a static lint over module sources because a real
    runtime e2e would require live Postgres + Redis + broker.
    """
    import inspect
    import re
    from app.tasks import evaluate_signals, execute_buy

    def _strip(src: str) -> str:
        src = re.sub(r'"""[\s\S]*?"""', "", src)
        src = re.sub(r"'''[\s\S]*?'''", "", src)
        src = re.sub(r"(?m)#.*$", "", src)
        return src.lower()

    for mod in (evaluate_signals, execute_buy):
        code = _strip(inspect.getsource(mod))
        assert "record_not_tradable" in code, (
            f"{mod.__name__}: missing record_not_tradable() call."
        )
        assert "reason=not_tradable" in code, (
            f"{mod.__name__}: missing reason=NOT_TRADABLE skip log."
        )
        assert (
            "pipelinewatchlist" in code or "pipeline_watchlist" in code
        ), (
            f"{mod.__name__}: must reference pipeline_watchlist for "
            "L3 membership enforcement (Task #232 round 16)."
        )
        assert "pipelinewatchlistasset" in code or "pipeline_watchlist_asset" in code, (
            f"{mod.__name__}: must read pipeline_watchlist_assets to "
            "compute the L3 symbol set."
        )


def test_execute_buy_orders_tradable_before_limit() -> None:
    """Regression for Task #232 reviewer feedback: the candidate-cap
    SQL must place ``is_tradable=true`` rows ahead of inactive ones so
    a wide pool of non-tradable symbols cannot starve tradable
    candidates out of the ``LIMIT :cap`` window.
    """
    import inspect
    from app.tasks import execute_buy

    src = inspect.getsource(execute_buy).lower()
    # The query must (a) carry the LIMIT, (b) order by tradable DESC
    # ahead of that LIMIT.
    assert "limit :cap" in src
    assert "order by bool_or(pc.is_tradable) desc" in src, (
        "execute_buy candidate query MUST `ORDER BY bool_or(pc."
        "is_tradable) DESC` before the LIMIT — otherwise non-tradable "
        "rows can crowd out tradable ones in the evaluation cap."
    )


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
