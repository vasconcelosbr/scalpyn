"""Prometheus counters for the orphan-transaction watchdog (Task #256).

Three labels keep the cardinality bounded while still letting the
operator distinguish:

* ``state`` — ``active`` | ``idle in transaction`` | ``idle in transaction (aborted)``.
  The 2026-05-10 incident proved the runbook filter that only covered
  ``idle in transaction*`` was insufficient — a session in ``state='active'``
  with an open SAVEPOINT held row-locks for 7h31min before manual rescue.
* ``app`` — ``application_name`` from ``pg_stat_activity``. Empty / unknown
  rows are bucketed as ``"unknown"`` so we never explode label cardinality.

Both metrics degrade to no-ops when ``prometheus_client`` is missing
(unit tests / dev shells). All call sites can call ``record_*`` without
guarding the optional dependency.
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
    logger.debug("prometheus_client unavailable: %s — orphan-tx metrics disabled", exc)


_KILLED: Optional["Counter"] = None
_SCAN_ERRORS: Optional["Counter"] = None
_SCANS: Optional["Counter"] = None


def _init() -> None:
    global _KILLED, _SCAN_ERRORS, _SCANS
    if not _PROM_OK or _KILLED is not None:
        return
    _KILLED = Counter(
        "scalpyn_orphan_tx_killed_total",
        "Postgres transactions terminated by the orphan-tx watchdog (Task #256).",
        ["state", "app"],
    )
    _SCAN_ERRORS = Counter(
        "scalpyn_orphan_tx_scan_errors_total",
        "Watchdog scans that failed (DB unreachable, permission denied, …).",
    )
    _SCANS = Counter(
        "scalpyn_orphan_tx_scans_total",
        "Watchdog scans completed successfully (no kills counted here).",
    )


def record_killed(state: str, app: str = "unknown", count: int = 1) -> None:
    if count <= 0:
        return
    _init()
    if _KILLED is None:
        return
    safe_state = state if state in {
        "active",
        "idle in transaction",
        "idle in transaction (aborted)",
    } else "other"
    safe_app = (app or "").strip()[:40] or "unknown"
    try:
        _KILLED.labels(state=safe_state, app=safe_app).inc(count)
    except Exception as exc:  # pragma: no cover — never let metrics raise
        logger.warning("orphan_tx_metrics: counter inc failed: %s", exc)


def record_scan(success: bool = True) -> None:
    _init()
    counter = _SCANS if success else _SCAN_ERRORS
    if counter is None:
        return
    try:
        counter.inc()
    except Exception as exc:  # pragma: no cover
        logger.warning("orphan_tx_metrics: scan counter inc failed: %s", exc)
