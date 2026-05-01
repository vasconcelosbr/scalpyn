"""Prometheus metrics for the robust indicator pipeline.

We register five metrics from the spec plus the divergence counter:

  * ``indicator_computation_duration_seconds`` — histogram, labels:
    ``symbol``, ``indicator``, ``source``.
  * ``indicator_confidence`` — gauge, label: ``symbol``.
  * ``indicator_staleness_seconds`` — gauge, labels: ``symbol``,
    ``indicator``.
  * ``score_rejection_total`` — counter, label: ``reason``.
  * ``robust_vs_legacy_divergence_total`` — counter, label: ``bucket``
    (``<1%``, ``1-5%``, ``5-10%``, ``>10%``).

The metrics live in a shared ``CollectorRegistry`` so they can be scraped
through the FastAPI ``/metrics`` endpoint regardless of which worker emitted
them. ``prometheus-client`` is an optional install — when missing, the
public functions degrade to no-ops so the whole shadow runner stays
non-fatal.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


try:
    from prometheus_client import (  # type: ignore[import-untyped]
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _PROMETHEUS_AVAILABLE = True
except Exception:  # pragma: no cover — exercised when extra is missing
    _PROMETHEUS_AVAILABLE = False
    CollectorRegistry = None  # type: ignore[assignment]
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


if _PROMETHEUS_AVAILABLE:
    REGISTRY = CollectorRegistry()

    INDICATOR_COMPUTATION_DURATION = Histogram(
        "indicator_computation_duration_seconds",
        "Time spent computing a robust indicator value.",
        labelnames=("symbol", "indicator", "source"),
        buckets=_BUCKETS,
        registry=REGISTRY,
    )
    INDICATOR_CONFIDENCE = Gauge(
        "indicator_confidence",
        "Average confidence of the latest robust indicator snapshot.",
        labelnames=("symbol",),
        registry=REGISTRY,
    )
    INDICATOR_STALENESS = Gauge(
        "indicator_staleness_seconds",
        "Latest staleness (seconds) of an indicator envelope.",
        labelnames=("symbol", "indicator"),
        registry=REGISTRY,
    )
    SCORE_REJECTION_TOTAL = Counter(
        "score_rejection_total",
        "Robust score-engine rejections, by reason.",
        labelnames=("reason",),
        registry=REGISTRY,
    )
    ROBUST_VS_LEGACY_DIVERGENCE_TOTAL = Counter(
        "robust_vs_legacy_divergence_total",
        "Bucketed |robust - legacy| / max(legacy, 1e-9) divergences.",
        labelnames=("bucket",),
        registry=REGISTRY,
    )
    ROBUST_SILENT_FALLBACK_TOTAL = Counter(
        "robust_silent_fallback_total",
        "Robust-engine score-selection failures, by reason. Phase 3: "
        "outside ``LEGACY_PIPELINE_ROLLBACK`` these no longer fall "
        "back to legacy — the result is a robust-tagged sentinel — "
        "but the counter remains the canonical signal for ops to see "
        "how often the robust engine cannot produce a value. The "
        "``legacy_rollback`` reason is bumped exactly once per call "
        "while the rollback flag is active.",
        labelnames=("reason",),
        registry=REGISTRY,
    )
else:  # pragma: no cover — exercised when extra is missing
    REGISTRY = None  # type: ignore[assignment]
    INDICATOR_COMPUTATION_DURATION = None
    INDICATOR_CONFIDENCE = None
    INDICATOR_STALENESS = None
    SCORE_REJECTION_TOTAL = None
    ROBUST_VS_LEGACY_DIVERGENCE_TOTAL = None
    ROBUST_SILENT_FALLBACK_TOTAL = None


# ── Process-local fallback counter (always available) ─────────────────────
# Keeps a per-reason tally even when prometheus-client is missing so the
# admin endpoint can still surface the silent-fallback rate.
_SILENT_FALLBACK_LOCAL: dict[str, int] = {}


def observe_compute_duration(
    symbol: str,
    indicator: str,
    source: str,
    seconds: float,
) -> None:
    if INDICATOR_COMPUTATION_DURATION is None:
        return
    try:
        INDICATOR_COMPUTATION_DURATION.labels(symbol=symbol, indicator=indicator, source=source).observe(seconds)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("metrics: observe_compute_duration failed: %s", exc)


def set_indicator_confidence(symbol: str, confidence: float) -> None:
    if INDICATOR_CONFIDENCE is None:
        return
    try:
        INDICATOR_CONFIDENCE.labels(symbol=symbol).set(float(confidence))
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("metrics: set_indicator_confidence failed: %s", exc)


def set_indicator_staleness(symbol: str, indicator: str, seconds: float) -> None:
    if INDICATOR_STALENESS is None:
        return
    try:
        INDICATOR_STALENESS.labels(symbol=symbol, indicator=indicator).set(float(seconds))
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("metrics: set_indicator_staleness failed: %s", exc)


def increment_rejection(reason: str) -> None:
    if SCORE_REJECTION_TOTAL is None:
        return
    try:
        SCORE_REJECTION_TOTAL.labels(reason=reason).inc()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("metrics: increment_rejection failed: %s", exc)


def divergence_bucket(legacy_score: Optional[float], robust_score: Optional[float]) -> str:
    """Return the divergence bucket label for two scores."""
    if legacy_score is None or robust_score is None:
        return "unknown"
    base = max(abs(float(legacy_score)), 1e-9)
    diff_pct = abs(float(legacy_score) - float(robust_score)) / base * 100.0
    if diff_pct < 1.0:
        return "<1%"
    if diff_pct < 5.0:
        return "1-5%"
    if diff_pct < 10.0:
        return "5-10%"
    return ">10%"


def increment_divergence(bucket: str) -> None:
    if ROBUST_VS_LEGACY_DIVERGENCE_TOTAL is None:
        return
    try:
        ROBUST_VS_LEGACY_DIVERGENCE_TOTAL.labels(bucket=bucket).inc()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("metrics: increment_divergence failed: %s", exc)


def increment_silent_fallback(reason: str) -> None:
    """Bump the silent-fallback counter for ``reason``.

    Always records into the in-process tally (so the admin endpoint can
    surface fallback rates even without prometheus-client installed),
    and additionally bumps the Prometheus counter when available.
    """
    label = (reason or "unknown")[:60]
    _SILENT_FALLBACK_LOCAL[label] = _SILENT_FALLBACK_LOCAL.get(label, 0) + 1
    if ROBUST_SILENT_FALLBACK_TOTAL is None:
        return
    try:
        ROBUST_SILENT_FALLBACK_TOTAL.labels(reason=label).inc()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("metrics: increment_silent_fallback failed: %s", exc)


def silent_fallback_snapshot() -> dict[str, int]:
    """Return a copy of the in-process silent-fallback tally."""
    return dict(_SILENT_FALLBACK_LOCAL)


def reset_silent_fallback() -> None:
    """Reset the in-process silent-fallback tally (test helper)."""
    _SILENT_FALLBACK_LOCAL.clear()


def render_metrics() -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for the FastAPI handler."""
    if not _PROMETHEUS_AVAILABLE or REGISTRY is None:
        msg = (
            b"# prometheus-client not installed; install the dependency to enable /metrics.\n"
        )
        return msg, "text/plain; charset=utf-8"
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


__all__ = [
    "CONTENT_TYPE_LATEST",
    "REGISTRY",
    "divergence_bucket",
    "increment_divergence",
    "increment_rejection",
    "increment_silent_fallback",
    "observe_compute_duration",
    "render_metrics",
    "reset_silent_fallback",
    "set_indicator_confidence",
    "set_indicator_staleness",
    "silent_fallback_snapshot",
]
