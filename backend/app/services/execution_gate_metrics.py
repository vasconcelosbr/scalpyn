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

Stage-label taxonomy (Task #232, reviewer round 13)
====================================================
The ``stage`` / ``from_stage`` / ``to_stage`` labels here use the
**transition names** (``pool``, ``metadata``, ``profile_filter``,
``blocking``) that match the actual function boundaries inside
``pipeline_scan`` rather than the abstract POOL/L1/L2/L3 framing
used in operator runbooks. The mapping is:

  * ``pool``           ↔ POOL (operator-curated ``pool_coins``)
  * ``metadata``       ↔ L1 metadata enrichment
  * ``profile_filter`` ↔ L2 profile-discovery filter
  * ``blocking``       ↔ L3 trading-eligibility gate

Dashboards that want the L1/L2/L3 framing should use Grafana's
``label_replace`` rather than re-labeling at the metric-emission
site, so the source of truth here stays aligned with the code paths
it instruments.
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
        # Rate is keyed by ``{stage,reason}`` so dashboards can chart
        # both the per-stage aggregate (``reason="any"``) and the
        # per-reason breakdown coming from the rejection tracker.
        _PIPELINE_REJECTION = Gauge(
            "scalpyn_pipeline_rejection_rate",
            "Fraction of pipeline_scan candidates rejected (0.0-1.0).",
            ["stage", "reason"],
        )
        _COLLECT_UNIVERSE = Gauge(
            "scalpyn_collect_universe_size",
            "Active spot symbols processed by collect_market_data (Task #232).",
        )
    # Throughput keyed by ``{from_stage,to_stage}`` so each funnel
    # transition is its own time-series — matches the audit narrative
    # "X candidates entered stage A, Y survived to stage B".
    _PIPELINE_THROUGHPUT = Counter(
        "scalpyn_pipeline_throughput_total",
        "Symbols that survived a pipeline_scan transition (Task #232).",
        ["from_stage", "to_stage"],
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


# Task #232 round 18 — single source of truth for the stage label
# taxonomy. Internal call sites still pass the transition names
# (``pool``/``metadata``/``profile_filter``/``blocking``) that match
# the actual code boundaries inside ``pipeline_scan``; the metric
# helpers below normalise to the runbook/dashboard taxonomy
# (``POOL``/``L1``/``L2``/``L3``) before emitting so operators don't
# have to do ``label_replace`` in PromQL.
_STAGE_LABEL_ALIASES: dict[str, str] = {
    "pool":            "POOL",
    "metadata":        "L1",
    "profile_filter":  "L2",
    "blocking":        "L3",
}


def _canonical_stage(stage: str) -> str:
    """Map an internal transition name to the runbook L1/L2/L3 label.

    Already-canonical labels (``POOL``/``L1``/``L2``/``L3``) and any
    unknown value are returned unchanged so the helper is safe for
    ad-hoc call sites.
    """
    if stage is None:
        return stage
    return _STAGE_LABEL_ALIASES.get(stage.lower(), stage)


def record_pipeline_stage(
    from_stage: str,
    to_stage: str,
    entered: int,
    survived: int,
) -> None:
    """Publish the funnel metrics for one ``from_stage → to_stage`` transition.

    Stage labels are normalised to the runbook taxonomy
    (``POOL``/``L1``/``L2``/``L3``) before emit so dashboards and
    Grafana alerts can use the documented names directly.

    * ``scalpyn_pipeline_universe_size{stage=from_stage}`` — entered count.
    * ``scalpyn_pipeline_throughput_total{from_stage,to_stage}`` — survived (monotonic counter).
    * ``scalpyn_pipeline_rejection_rate{stage=from_stage,reason="any"}``
      — aggregate (entered-survived)/entered. Per-reason breakdown is
      published separately via :func:`record_pipeline_rejection_reason`.
    """
    _init()
    fs = _canonical_stage(from_stage)
    ts = _canonical_stage(to_stage)
    try:
        if _PIPELINE_UNIVERSE is not None:
            _PIPELINE_UNIVERSE.labels(stage=fs).set(max(0, int(entered)))
        if _PIPELINE_THROUGHPUT is not None and survived > 0:
            _PIPELINE_THROUGHPUT.labels(
                from_stage=fs, to_stage=ts,
            ).inc(int(survived))
        if _PIPELINE_REJECTION is not None:
            rate = 0.0
            if entered > 0:
                rate = max(0.0, min(1.0, 1.0 - (survived / entered)))
            _PIPELINE_REJECTION.labels(stage=fs, reason="any").set(rate)
    except Exception as exc:  # pragma: no cover
        logger.debug("pipeline stage metrics failed (%s→%s): %s", fs, ts, exc)


def record_pipeline_rejection_reason(
    stage: str,
    reason: str,
    rejected: int,
    entered: int,
) -> None:
    """Publish ``scalpyn_pipeline_rejection_rate{stage,reason}`` for one reason.

    ``stage`` is normalised to the runbook L1/L2/L3 taxonomy.
    """
    if entered <= 0 or _PIPELINE_REJECTION is None:
        _init()
    if _PIPELINE_REJECTION is None:
        return
    try:
        rate = 0.0
        if entered > 0:
            rate = max(0.0, min(1.0, rejected / entered))
        _PIPELINE_REJECTION.labels(
            stage=_canonical_stage(stage), reason=reason,
        ).set(rate)
    except Exception as exc:  # pragma: no cover
        logger.debug("rejection reason metric failed (%s/%s): %s", stage, reason, exc)


def record_collect_universe(active_count: int) -> None:
    """Set the gauge of active spot symbols processed by collect_market_data."""
    _init()
    if _COLLECT_UNIVERSE is None:
        return
    try:
        _COLLECT_UNIVERSE.set(max(0, int(active_count)))
    except Exception as exc:  # pragma: no cover
        logger.debug("collect universe gauge set failed: %s", exc)
