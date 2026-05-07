"""Prometheus metrics for OHLCV ingestion (Task #234 hotfix).

Three counters/histograms exposed on ``/metrics``:

  * ``scalpyn_ohlcv_received_total{symbol,timeframe}`` — Counter incremented
    once per fetch_ohlcv response. Labels are bounded by the active pool
    (currently BTC-only) so cardinality stays small.
  * ``scalpyn_ohlcv_persisted_total{symbol,timeframe}`` — Counter
    incremented per successful UPSERT batch (one per symbol per cycle).
  * ``scalpyn_ohlcv_latest_age_seconds{symbol,timeframe}`` — Gauge of the
    age (now - latest_candle_time) immediately after persistence; primary
    signal for the ``ingestion_stale`` alert.

Operators alert on:
  * ``rate(scalpyn_ohlcv_received_total[10m]) == 0`` for any active symbol.
  * ``scalpyn_ohlcv_latest_age_seconds > 1800`` for any active symbol.

Runbook: ``backend/docs/runbooks/btc-only-stabilization.md``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge  # type: ignore[import-untyped]

    _OHLCV_RECEIVED = Counter(
        "scalpyn_ohlcv_received_total",
        "OHLCV candles received from exchange (per fetch). "
        "Labels: symbol, timeframe.",
        ["symbol", "timeframe"],
    )
    _OHLCV_PERSISTED = Counter(
        "scalpyn_ohlcv_persisted_total",
        "OHLCV rows persisted (UPSERT) into the ohlcv table. "
        "Labels: symbol, timeframe.",
        ["symbol", "timeframe"],
    )
    _OHLCV_LATEST_AGE = Gauge(
        "scalpyn_ohlcv_latest_age_seconds",
        "Age in seconds of the latest persisted candle (now - max(time)). "
        "Labels: symbol, timeframe.",
        ["symbol", "timeframe"],
    )
except Exception:  # pragma: no cover — prometheus_client may be absent in tests
    _OHLCV_RECEIVED = None
    _OHLCV_PERSISTED = None
    _OHLCV_LATEST_AGE = None


def record_received(symbol: str, timeframe: str, n: int) -> None:
    if _OHLCV_RECEIVED is None or n <= 0:
        return
    try:
        _OHLCV_RECEIVED.labels(symbol=symbol, timeframe=timeframe).inc(n)
    except Exception as exc:  # pragma: no cover
        logger.debug("[ohlcv_metrics] received inc failed: %s", exc)


def record_persisted(symbol: str, timeframe: str, n: int) -> None:
    if _OHLCV_PERSISTED is None or n <= 0:
        return
    try:
        _OHLCV_PERSISTED.labels(symbol=symbol, timeframe=timeframe).inc(n)
    except Exception as exc:  # pragma: no cover
        logger.debug("[ohlcv_metrics] persisted inc failed: %s", exc)


def record_latest_age(symbol: str, timeframe: str, age_seconds: float) -> None:
    if _OHLCV_LATEST_AGE is None:
        return
    try:
        _OHLCV_LATEST_AGE.labels(symbol=symbol, timeframe=timeframe).set(
            max(0.0, float(age_seconds))
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("[ohlcv_metrics] latest_age set failed: %s", exc)
