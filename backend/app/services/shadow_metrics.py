"""Prometheus metrics for shadow trade resolution (Task #303).

Counter ``scalpyn_shadow_resolved_source_total{source}`` records which of
the three fallback levels actually produced the decision used to create a
shadow trade in ``safe_backfill_watchlist_shadows``:

* ``recent_log``  — ``decisions_log`` row inside the 10 min window.
* ``stale_log``   — last ALLOW/SPOT in ``decisions_log`` with no time window.
* ``live_l3``     — synthetic decision built from the live L3 snapshot
  (``pipeline_watchlist_assets``) because no DecisionLog ever existed for
  this user+symbol (chronically-approved symbol that never transitioned
  after ``_should_log_decision`` was tightened).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter  # type: ignore[import-untyped]

    _RESOLVED_SOURCE = Counter(
        "scalpyn_shadow_resolved_source_total",
        "Number of shadow trades created by source of the resolved decision. "
        "Labels: source in {recent_log, stale_log, live_l3}.",
        ["source"],
    )
except Exception:  # pragma: no cover — prometheus_client may be absent
    _RESOLVED_SOURCE = None


_VALID_SOURCES = {"recent_log", "stale_log", "live_l3"}


def record_resolved_source(source: str) -> None:
    """Increment the resolved-source counter. Never raises."""
    if source not in _VALID_SOURCES:
        logger.warning(
            "[shadow_metrics] ignoring unknown source=%r (valid=%s)",
            source, sorted(_VALID_SOURCES),
        )
        return
    if _RESOLVED_SOURCE is None:
        return
    try:
        _RESOLVED_SOURCE.labels(source=source).inc()
    except Exception as exc:  # pragma: no cover
        logger.debug("[shadow_metrics] inc failed: %s", exc)
