"""Prometheus metrics for WebSocket endpoints (Task #234 hotfix).

Metric:
  ``ws_degraded_seconds{endpoint}`` — Gauge tracking the cumulative seconds
  any single WebSocket connection has spent in degraded mode (broker
  unreachable, manager registry corrupted, etc.). Operators alert on a
  sustained non-zero rate per endpoint over a 10-minute window — see
  ``backend/docs/runbooks/btc-only-stabilization.md``.

The gauge is set on a per-connection basis using ``set()``; aggregations
across connections are done in PromQL via ``sum() by (endpoint)``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge  # type: ignore[import-untyped]

    _WS_DEGRADED_SECONDS = Counter(
        "ws_degraded_seconds",
        "Cumulative seconds spent in degraded mode by WebSocket endpoints. "
        "Labels: endpoint.",
        ["endpoint"],
    )
    _WS_DEGRADED_NOW = Gauge(
        "ws_degraded_active",
        "Number of WebSocket connections currently in degraded mode. "
        "Labels: endpoint.",
        ["endpoint"],
    )
except Exception:  # pragma: no cover — prometheus_client may be absent in tests
    _WS_DEGRADED_SECONDS = None
    _WS_DEGRADED_NOW = None


def record_degraded_seconds(endpoint: str, seconds: float) -> None:
    if _WS_DEGRADED_SECONDS is None or seconds <= 0:
        return
    try:
        _WS_DEGRADED_SECONDS.labels(endpoint=endpoint).inc(seconds)
    except Exception as exc:  # pragma: no cover
        logger.debug("[ws_metrics] inc failed: %s", exc)


def set_degraded_active(endpoint: str, delta: int) -> None:
    if _WS_DEGRADED_NOW is None:
        return
    try:
        if delta >= 0:
            _WS_DEGRADED_NOW.labels(endpoint=endpoint).inc(delta)
        else:
            _WS_DEGRADED_NOW.labels(endpoint=endpoint).dec(-delta)
    except Exception as exc:  # pragma: no cover
        logger.debug("[ws_metrics] gauge update failed: %s", exc)
