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


try:
    from prometheus_client import Gauge  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    Gauge = None  # type: ignore[assignment]


_NOT_TRADABLE: Optional["Counter"] = None
_ORPHANS_CLEANED: Optional["Counter"] = None
_PIPELINE_UNIVERSE: Optional["Gauge"] = None
_PIPELINE_THROUGHPUT: Optional["Counter"] = None
_PIPELINE_REJECTION: Optional["Gauge"] = None
_COLLECT_UNIVERSE: Optional["Gauge"] = None


def _init() -> None:
    global _NOT_TRADABLE, _ORPHANS_CLEANED
    global _PIPELINE_UNIVERSE, _PIPELINE_THROUGHPUT, _PIPELINE_REJECTION, _COLLECT_UNIVERSE
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
    # Task #232 — pipeline auditability metrics. The funnel publishes
    # one observation per stage per scan so the dashboards can chart
    # universe → throughput → rejection ratio without re-deriving from
    # log lines.
    if Gauge is not None:
        _PIPELINE_UNIVERSE = Gauge(
            "scalpyn_pipeline_universe_size",
            "Active symbols entering pipeline_scan per stage (Task #232).",
            ["stage"],
        )
        _PIPELINE_REJECTION = Gauge(
            "scalpyn_pipeline_rejection_rate",
            "Fraction of pipeline_scan candidates rejected per stage (0.0-1.0).",
            ["stage"],
        )
        _COLLECT_UNIVERSE = Gauge(
            "scalpyn_collect_universe_size",
            "Active spot symbols processed by collect_market_data (Task #232).",
        )
    _PIPELINE_THROUGHPUT = Counter(
        "scalpyn_pipeline_throughput_total",
        "Symbols that survived a pipeline_scan stage (Task #232).",
        ["stage"],
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


def record_pipeline_stage(stage: str, entered: int, survived: int) -> None:
    """Publish the three pipeline funnel metrics for a single stage.

    * ``scalpyn_pipeline_universe_size{stage}`` — symbols that ENTERED.
    * ``scalpyn_pipeline_throughput_total{stage}`` — counter of symbols
      that SURVIVED (monotonic).
    * ``scalpyn_pipeline_rejection_rate{stage}`` — (entered-survived)/entered.
    """
    _init()
    try:
        if _PIPELINE_UNIVERSE is not None:
            _PIPELINE_UNIVERSE.labels(stage=stage).set(max(0, int(entered)))
        if _PIPELINE_THROUGHPUT is not None and survived > 0:
            _PIPELINE_THROUGHPUT.labels(stage=stage).inc(int(survived))
        if _PIPELINE_REJECTION is not None:
            rate = 0.0
            if entered > 0:
                rate = max(0.0, min(1.0, 1.0 - (survived / entered)))
            _PIPELINE_REJECTION.labels(stage=stage).set(rate)
    except Exception as exc:  # pragma: no cover
        logger.debug("pipeline stage metrics failed (%s): %s", stage, exc)


def record_collect_universe(active_count: int) -> None:
    """Set the gauge of active spot symbols processed by collect_market_data."""
    _init()
    if _COLLECT_UNIVERSE is None:
        return
    try:
        _COLLECT_UNIVERSE.set(max(0, int(active_count)))
    except Exception as exc:  # pragma: no cover
        logger.debug("collect universe gauge set failed: %s", exc)
