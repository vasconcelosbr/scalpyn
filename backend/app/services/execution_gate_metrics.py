"""Prometheus counters for the Task #232 execution-gate split.

Two failure modes need to remain visible:

* ``scalpyn_signals_skipped_not_tradable_total`` — a symbol scored well
  enough to be a buy candidate but the operator has not yet flipped
  ``pool_coins.is_tradable`` to true. Without this counter the SQL
  pre-filter at execution-side would silently hide every "scored but
  unauthorised" event from the dashboards, defeating the audit purpose
  of the split.
* ``scalpyn_pipeline_orphans_cleaned_total`` — rows in
  ``pipeline_watchlist_assets`` whose backing ``pool_coins`` row was
  deleted between two scans. The pipeline funnel cleans them up at
  the start of every cycle and reports how many were removed.

Both counters degrade to no-ops when ``prometheus_client`` is not
installed (tests / dev shells). Importers can always call
``record_*`` without guarding the optional dependency.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter  # type: ignore[import-untyped]
    _PROM_OK = True
except Exception as exc:  # pragma: no cover — optional dep
    Counter = None  # type: ignore[assignment]
    _PROM_OK = False
    logger.debug("prometheus_client unavailable: %s — execution-gate metrics disabled", exc)


_NOT_TRADABLE: Optional["Counter"] = None
_ORPHANS_CLEANED: Optional["Counter"] = None


def _init() -> None:
    global _NOT_TRADABLE, _ORPHANS_CLEANED
    if not _PROM_OK or _NOT_TRADABLE is not None:
        return
    _NOT_TRADABLE = Counter(
        "scalpyn_signals_skipped_not_tradable_total",
        "Buy signals skipped because pool_coins.is_tradable is false (Task #232).",
        ["task"],
    )
    _ORPHANS_CLEANED = Counter(
        "scalpyn_pipeline_orphans_cleaned_total",
        "pipeline_watchlist_assets rows deleted because their pool_coins row no longer exists.",
    )


def record_not_tradable(task: str, count: int = 1) -> None:
    """Increment the NOT_TRADABLE skip counter for ``task``."""
    if count <= 0:
        return
    _init()
    if _NOT_TRADABLE is None:
        return
    try:
        _NOT_TRADABLE.labels(task=task).inc(count)
    except Exception as exc:  # pragma: no cover
        logger.debug("not_tradable counter inc failed: %s", exc)


def record_orphans_cleaned(count: int) -> None:
    """Increment the orphan cleanup counter."""
    if count <= 0:
        return
    _init()
    if _ORPHANS_CLEANED is None:
        return
    try:
        _ORPHANS_CLEANED.inc(count)
    except Exception as exc:  # pragma: no cover
        logger.debug("orphans counter inc failed: %s", exc)
