"""Celery task: periodic symbol-ingestion audit (Task #194 — etapa 6).

Runs the same classifier the admin endpoint and CLI use, but in
``monitor_only=True`` mode by default so it never mutates state.  Three
alert classes are emitted, with deduplication keys held in Redis to
avoid alert storms:

* ``[POOL-AUDIT WARN]``       — ≥ 1 symbol classified NOT_APPROVED.
                                 Dedup key: 10 min.
* ``[WS-AUDIT CRITICAL]``     — ≥ 1 symbol classified NOT_SUBSCRIBED
                                 (resolver drift). Dedup key: 10 min.
* ``[REDIS-FALLBACK INFO]``   — aggregated count of NO_REDIS_DATA over
                                 the cycle. Dedup window: 5 min.

Operators flip the audit to active-repair mode by setting the env var
``SYMBOL_AUDIT_REPAIR=1`` — the task then calls
:class:`SymbolRemediator` with ``dry_run=False``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from .celery_app import celery_app

logger = logging.getLogger(__name__)


_DEDUP_TTL_POOL_AUDIT_SECONDS = 600
_DEDUP_TTL_WS_AUDIT_SECONDS = 600
_DEDUP_TTL_REDIS_FALLBACK_SECONDS = 300


async def _alert_dedup(redis, key: bytes, ttl: int) -> bool:
    """Return ``True`` iff this is the first call in the dedup window."""
    if redis is None:
        return True
    try:
        ok = await redis.set(key, b"1", nx=True, ex=ttl)
        return bool(ok)
    except Exception as exc:
        logger.debug("[symbol-audit] dedup set failed: %s", exc)
        return True


async def _audit_async(monitor_only: bool) -> dict:
    from ..services.redis_client import get_async_redis
    from ..services.symbol_health_service import (
        STATUS_NOT_APPROVED,
        STATUS_NOT_SUBSCRIBED,
        STATUS_NO_REDIS_DATA,
        STATUS_NO_INDICATOR_DATA,
        STATUS_OK,
        SymbolHealthService,
    )
    from ..services.symbol_remediator import (
        GateSymbolValidator,
        SymbolRemediator,
    )

    health = SymbolHealthService()
    report = await health.audit()
    counts = report.counts

    redis = await get_async_redis()

    # ── POOL-AUDIT WARN ──────────────────────────────────────────────
    if counts.get(STATUS_NOT_APPROVED, 0) > 0:
        if await _alert_dedup(redis, b"alert:symbol_audit:pool", _DEDUP_TTL_POOL_AUDIT_SECONDS):
            logger.warning(
                "[POOL-AUDIT WARN] %d symbols are NOT_APPROVED in pool_coins (run "
                "`python -m scripts.symbol_health_audit --no-approve --dry-run` for the list)",
                counts[STATUS_NOT_APPROVED],
            )

    # ── WS-AUDIT CRITICAL (resolver drift) ───────────────────────────
    if counts.get(STATUS_NOT_SUBSCRIBED, 0) > 0:
        if await _alert_dedup(redis, b"alert:symbol_audit:ws", _DEDUP_TTL_WS_AUDIT_SECONDS):
            logger.critical(
                "[WS-AUDIT CRITICAL] %d approved symbols are NOT_SUBSCRIBED — "
                "WS leader resolver drift; trades_buffer will stay empty for them",
                counts[STATUS_NOT_SUBSCRIBED],
            )

    # ── REDIS-FALLBACK INFO (aggregated) ─────────────────────────────
    if counts.get(STATUS_NO_REDIS_DATA, 0) > 0:
        if await _alert_dedup(redis, b"alert:symbol_audit:redis_fallback",
                              _DEDUP_TTL_REDIS_FALLBACK_SECONDS):
            logger.info(
                "[REDIS-FALLBACK INFO] %d symbols using REST fallback "
                "(no recent trade in trades_buffer)",
                counts[STATUS_NO_REDIS_DATA],
            )

    logger.info(
        "[POOL] symbol-audit: total=%d ok=%d not_approved=%d not_subscribed=%d "
        "no_redis=%d no_indicator=%d",
        report.total,
        counts.get(STATUS_OK, 0),
        counts.get(STATUS_NOT_APPROVED, 0),
        counts.get(STATUS_NOT_SUBSCRIBED, 0),
        counts.get(STATUS_NO_REDIS_DATA, 0),
        counts.get(STATUS_NO_INDICATOR_DATA, 0),
    )

    remediation: Optional[dict] = None
    if not monitor_only:
        remediator = SymbolRemediator(validator=GateSymbolValidator())
        rem = await remediator.remediate(report, dry_run=False)
        remediation = rem.to_dict()
        logger.info(
            "[AUDIT-FIX] remediation actions=%d (counts=%s, refresh=%s, recompute=%s)",
            rem.total_actions,
            rem.counts_by_action,
            rem.refresh_subscriptions_requested,
            rem.recompute_enqueued,
        )

    return {
        "monitor_only": monitor_only,
        "report": report.to_dict(),
        "remediation": remediation,
    }


@celery_app.task(name="app.tasks.symbol_health_audit.monitor_only")
def monitor_only() -> dict:
    """Beat-driven monitor-only audit (no DB/Redis writes)."""
    repair = os.environ.get("SYMBOL_AUDIT_REPAIR", "").strip() == "1"
    return asyncio.run(_audit_async(monitor_only=not repair))


@celery_app.task(name="app.tasks.symbol_health_audit.run_repair")
def run_repair() -> dict:
    """Full audit + repair, regardless of ``SYMBOL_AUDIT_REPAIR``."""
    return asyncio.run(_audit_async(monitor_only=False))


__all__ = ["monitor_only", "run_repair"]
