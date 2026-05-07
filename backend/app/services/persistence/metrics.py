"""Prometheus metrics for the persistence queue.

Degrades gracefully when ``prometheus_client`` is not installed: every
record_* helper becomes a no-op so the queue keeps working in environments
that do not collect metrics (notably tests and dev shells).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram  # type: ignore[import-untyped]
    _PROM_OK = True
except Exception as exc:  # pragma: no cover — optional dep
    Counter = Gauge = Histogram = None  # type: ignore[assignment]
    _PROM_OK = False
    logger.debug("prometheus_client unavailable: %s — persistence metrics disabled", exc)


_QUEUE_DEPTH: Optional["Gauge"] = None
_ENQUEUED: Optional["Counter"] = None
_DEQUEUED: Optional["Counter"] = None
_DROPPED: Optional["Counter"] = None
_COMMIT_LATENCY: Optional["Histogram"] = None
_QUEUE_LATENCY: Optional["Histogram"] = None
_RETRIES: Optional["Counter"] = None
_ERRORS: Optional["Counter"] = None
_WORKERS_BUSY: Optional["Gauge"] = None


def _init_metrics() -> None:
    global _QUEUE_DEPTH, _ENQUEUED, _DEQUEUED, _DROPPED
    global _COMMIT_LATENCY, _QUEUE_LATENCY, _RETRIES, _ERRORS, _WORKERS_BUSY
    if not _PROM_OK or _QUEUE_DEPTH is not None:
        return
    _QUEUE_DEPTH = Gauge(
        "scalpyn_persistence_queue_depth",
        "Current number of pending messages in the persistence queue",
        ["category"],
    )
    _ENQUEUED = Counter(
        "scalpyn_persistence_enqueued_total",
        "Messages enqueued",
        ["category", "kind"],
    )
    _DEQUEUED = Counter(
        "scalpyn_persistence_dequeued_total",
        "Messages dequeued and processed",
        ["category", "kind", "outcome"],  # outcome: ok | retry | failed
    )
    _DROPPED = Counter(
        "scalpyn_persistence_dropped_total",
        "Messages dropped due to backpressure",
        ["category", "kind", "reason"],  # reason: queue_full | shutdown
    )
    _COMMIT_LATENCY = Histogram(
        "scalpyn_persistence_commit_latency_seconds",
        "Time from worker dequeue to successful commit",
        ["kind"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
    _QUEUE_LATENCY = Histogram(
        "scalpyn_persistence_queue_latency_seconds",
        "Time from enqueue to dequeue (queue waiting time)",
        ["category"],
        buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0, 60.0, 300.0),
    )
    _RETRIES = Counter(
        "scalpyn_persistence_retries_total",
        "Retries triggered by transient errors",
        ["kind", "error_class"],
    )
    _ERRORS = Counter(
        "scalpyn_persistence_errors_total",
        "Permanent failures (retries exhausted or non-transient)",
        ["kind", "error_class"],
    )
    _WORKERS_BUSY = Gauge(
        "scalpyn_persistence_workers_busy",
        "Workers currently processing a message",
    )


_init_metrics()


def record_enqueue(category: str, kind: str) -> None:
    if _ENQUEUED is None:
        return
    try:
        _ENQUEUED.labels(category=category, kind=kind).inc()
    except Exception:
        pass


def record_dequeue(category: str, kind: str, outcome: str, queue_wait_s: float) -> None:
    if _DEQUEUED is None:
        return
    try:
        _DEQUEUED.labels(category=category, kind=kind, outcome=outcome).inc()
        if _QUEUE_LATENCY is not None:
            _QUEUE_LATENCY.labels(category=category).observe(max(0.0, queue_wait_s))
    except Exception:
        pass


def record_drop(category: str, kind: str, reason: str) -> None:
    if _DROPPED is None:
        return
    try:
        _DROPPED.labels(category=category, kind=kind, reason=reason).inc()
    except Exception:
        pass


def record_commit_latency(kind: str, seconds: float) -> None:
    if _COMMIT_LATENCY is None:
        return
    try:
        _COMMIT_LATENCY.labels(kind=kind).observe(max(0.0, seconds))
    except Exception:
        pass


def record_retry(kind: str, error_class: str) -> None:
    if _RETRIES is None:
        return
    try:
        _RETRIES.labels(kind=kind, error_class=error_class).inc()
    except Exception:
        pass


def record_error(kind: str, error_class: str) -> None:
    if _ERRORS is None:
        return
    try:
        _ERRORS.labels(kind=kind, error_class=error_class).inc()
    except Exception:
        pass


def set_queue_depth(category: str, depth: int) -> None:
    if _QUEUE_DEPTH is None:
        return
    try:
        _QUEUE_DEPTH.labels(category=category).set(depth)
    except Exception:
        pass


def inc_workers_busy(delta: int) -> None:
    if _WORKERS_BUSY is None:
        return
    try:
        _WORKERS_BUSY.inc(delta)
    except Exception:
        pass
