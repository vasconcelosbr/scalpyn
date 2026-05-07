"""Prometheus metrics for the simulation service (Task #234 hotfix).

Kept in a tiny dedicated module so importing it never drags in heavy
SimulationService dependencies (SQLAlchemy session, repository, engine)
into request paths that just want to record a skip.

Metric:
  ``simulation_skipped_total{reason,exchange}`` — Counter incremented
  every time ``run_simulation_batch`` returns ``{"status":"skipped"}``.
  Reasons currently emitted:
    * ``no_recent_candles``     — no OHLCV in last 24h for the target exchange.
    * ``insufficient_candles``  — fewer than 100 candles in last 24h.

Operators alert on a sustained non-zero rate per (reason, exchange).  See
``backend/docs/runbooks/btc-only-stabilization.md``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter  # type: ignore[import-untyped]

    _SIMULATION_SKIPPED = Counter(
        "simulation_skipped_total",
        "Number of simulation batches that returned status=skipped instead of "
        "raising RuntimeError. Labels: reason, exchange.",
        ["reason", "exchange"],
    )
    # Alias counter for the metric name spelled out in the original
    # Task #234 brief (`simulation_skips_total`). Both names are
    # incremented in lockstep so existing dashboards / new alerts
    # continue to work regardless of which spelling they use.
    _SIMULATION_SKIPS_ALIAS = Counter(
        "simulation_skips_total",
        "Alias of simulation_skipped_total — kept for dashboard "
        "compatibility with the Task #234 hotfix brief. Labels: reason, exchange.",
        ["reason", "exchange"],
    )
except Exception:  # pragma: no cover — prometheus_client may be absent in tests
    _SIMULATION_SKIPPED = None
    _SIMULATION_SKIPS_ALIAS = None


def record_simulation_skipped(*, reason: str, exchange: str) -> None:
    """Increment the skipped-batch counter. Never raises."""
    if _SIMULATION_SKIPPED is None:
        return
    try:
        _SIMULATION_SKIPPED.labels(reason=reason, exchange=exchange).inc()
        if _SIMULATION_SKIPS_ALIAS is not None:
            _SIMULATION_SKIPS_ALIAS.labels(reason=reason, exchange=exchange).inc()
    except Exception as exc:  # pragma: no cover
        logger.debug("[simulation_metrics] inc failed: %s", exc)
