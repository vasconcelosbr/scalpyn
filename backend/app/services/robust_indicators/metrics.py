"""Prometheus metrics for the robust indicator pipeline.

Exposed metrics:

  * ``indicator_computation_duration_seconds`` — histogram, labels:
    ``symbol``, ``indicator``, ``source``.
  * ``indicator_confidence`` — gauge, label: ``symbol``.
  * ``indicator_staleness_seconds`` — gauge, labels: ``symbol``,
    ``indicator``.
  * ``score_rejection_total`` — counter, label: ``reason``.
  * ``exchange_request_latency_seconds`` — histogram, label: ``exchange``.
    Wraps every Gate.io and Binance public REST call site so the Grafana
    Exchange-Status panel can compute p95 latency and request volume.
  * ``exchange_request_errors_total`` — counter, labels: ``exchange``,
    ``kind`` (``http`` for non-2xx HTTP responses, ``transport`` for
    network/timeout/cancel errors). Used by the same panel for the
    error-rate column and the global error-rate alert.

The metrics live in a shared ``CollectorRegistry`` so they can be scraped
through the FastAPI ``/metrics`` endpoint regardless of which worker emitted
them. ``prometheus-client`` is an optional install — when missing, the
public functions degrade to no-ops.
"""

from __future__ import annotations

import logging

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
    EXCHANGE_REQUEST_LATENCY = Histogram(
        "exchange_request_latency_seconds",
        "Latency of public REST requests to a crypto exchange (Gate.io, Binance).",
        labelnames=("exchange",),
        buckets=_BUCKETS,
        registry=REGISTRY,
    )
    EXCHANGE_REQUEST_ERRORS = Counter(
        "exchange_request_errors_total",
        "Failed exchange REST requests, by exchange and error kind ('http' | 'transport').",
        labelnames=("exchange", "kind"),
        registry=REGISTRY,
    )
else:  # pragma: no cover — exercised when extra is missing
    REGISTRY = None  # type: ignore[assignment]
    INDICATOR_COMPUTATION_DURATION = None
    INDICATOR_CONFIDENCE = None
    INDICATOR_STALENESS = None
    SCORE_REJECTION_TOTAL = None
    EXCHANGE_REQUEST_LATENCY = None
    EXCHANGE_REQUEST_ERRORS = None


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


def observe_exchange_latency(exchange: str, seconds: float) -> None:
    """Record latency (seconds) of a single REST call to ``exchange``."""
    if EXCHANGE_REQUEST_LATENCY is None:
        return
    try:
        EXCHANGE_REQUEST_LATENCY.labels(exchange=exchange).observe(float(seconds))
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("metrics: observe_exchange_latency failed: %s", exc)


def increment_exchange_error(exchange: str, kind: str) -> None:
    """Increment the failed-request counter for ``exchange``.

    ``kind`` should be ``"http"`` for non-2xx HTTP responses and
    ``"transport"`` for network/timeout/cancel errors.
    """
    if EXCHANGE_REQUEST_ERRORS is None:
        return
    try:
        EXCHANGE_REQUEST_ERRORS.labels(exchange=exchange, kind=kind).inc()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("metrics: increment_exchange_error failed: %s", exc)


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
    "increment_exchange_error",
    "increment_rejection",
    "observe_compute_duration",
    "observe_exchange_latency",
    "render_metrics",
    "set_indicator_confidence",
    "set_indicator_staleness",
]
