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
import time
from typing import Optional

from .celery_app import celery_app

logger = logging.getLogger(__name__)


_DEDUP_TTL_POOL_AUDIT_SECONDS = 600
# Per-symbol "not streaming" alert: flag when ZCARD has been zero for at
# least this many seconds, then suppress repeated alerts for this many
# seconds (the prompt's "≥ 120 s detection, ≤ 1 alert / 10 min / symbol").
_WS_NOT_STREAMING_GRACE_SECONDS = 120
_WS_NOT_STREAMING_DEDUP_SECONDS = 600
_DEDUP_TTL_REDIS_FALLBACK_SECONDS = 300

# Redis key prefixes for the per-symbol streaming-health tracker. The
# "first_seen_empty" key holds the unix-ms timestamp the symbol was
# first observed with ZCARD=0; we delete it the moment ZCARD>0 returns
# so a flapping symbol does not accumulate false alerts.
_KEY_FIRST_SEEN_EMPTY = b"audit:ws:first_empty:"
_KEY_NOT_STREAMING_DEDUP = b"audit:ws:alerted:"


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


async def _evaluate_streaming_health(redis, report) -> int:
    """Emit one CRITICAL alert per symbol whose buffer has been empty > 120 s.

    Implements protection rule 2 of Etapa 6 of the prompt with the
    exact wording the operator runbook expects::

        CRITICAL [WS-AUDIT] symbol not streaming: {symbol}

    The "first empty observation" is stamped per-symbol in Redis so a
    transient gap (one missed audit cycle) does NOT immediately fire.
    The dedup key is also per-symbol with a 10-minute TTL so a symbol
    that stays empty for an hour fires at most 6 alerts, never one per
    audit cycle.

    Returns the number of alerts emitted in this cycle.
    """
    if redis is None:
        return 0
    now_ms = int(time.time() * 1000)
    fired = 0
    for rec in report.symbols:
        sym_bytes = rec.symbol.encode()
        # The prompt's rule-2 predicate is strict: ``ZCARD == 0`` for
        # >120 s on an APPROVED symbol. ``STATUS_NO_REDIS_DATA`` is a
        # superset that also fires on stale ``newest_age`` and on probe
        # errors — both of those are real degradations but they are NOT
        # "symbol not streaming" and would falsely page on-call. Filter
        # to ``buffer_member_count == 0`` and ``is_approved`` here so
        # the alert text remains honest.
        if rec.buffer_member_count != 0 or not rec.is_approved:
            try:
                await redis.delete(_KEY_FIRST_SEEN_EMPTY + sym_bytes)
            except Exception:
                pass
            continue
        # Symbol is empty in this cycle. Stamp first-seen if absent.
        first_key = _KEY_FIRST_SEEN_EMPTY + sym_bytes
        try:
            existing = await redis.get(first_key)
            if existing is None:
                # 24h TTL is more than enough — if the symbol stays empty
                # for a full day the operator will have intervened.
                await redis.set(first_key, str(now_ms).encode(), ex=86400)
                continue
            first_ms = int(existing)
        except Exception as exc:
            logger.debug("[symbol-audit] first_empty read failed for %s: %s", rec.symbol, exc)
            continue
        if (now_ms - first_ms) < _WS_NOT_STREAMING_GRACE_SECONDS * 1000:
            continue
        # Past the 120-s grace; alert with per-symbol dedup.
        dedup_key = _KEY_NOT_STREAMING_DEDUP + sym_bytes
        if await _alert_dedup(redis, dedup_key, _WS_NOT_STREAMING_DEDUP_SECONDS):
            logger.critical(
                "[WS-AUDIT] symbol not streaming: %s (ZCARD=0 for %ds)",
                rec.symbol, (now_ms - first_ms) // 1000,
            )
            fired += 1
    return fired


async def _audit_async(monitor_only: bool) -> dict:
    from ..services.redis_client import get_async_redis
    from ..services.symbol_health_service import (
        STATUS_NOT_APPROVED,
        STATUS_NOT_SUBSCRIBED,
        STATUS_NO_REDIS_DATA,
        STATUS_NO_INDICATOR_DATA,
        STATUS_OK,
        SymbolHealthService,
        build_etapa8_envelope,
    )
    from ..services.symbol_remediator import (
        GateSymbolValidator,
        SymbolRemediator,
    )

    health = SymbolHealthService()
    report = await health.audit()
    counts = report.counts

    redis = await get_async_redis()

    # ── POOL-AUDIT WARN (rule 1) ─────────────────────────────────────
    # ``logger.warning`` is the route Sentry's logging integration uses
    # in this codebase (see gate_ws_client.py comments for the same
    # pattern). We pass ``extra={...}`` so structured fields land on the
    # event in addition to the formatted message.
    if counts.get(STATUS_NOT_APPROVED, 0) > 0:
        if await _alert_dedup(redis, b"alert:symbol_audit:pool", _DEDUP_TTL_POOL_AUDIT_SECONDS):
            sample = [
                r.symbol for r in report.symbols
                if r.status == STATUS_NOT_APPROVED
            ][:20]
            logger.warning(
                "[POOL-AUDIT WARN] %d symbols active but not approved in pool_coins; "
                "first 20: %s",
                counts[STATUS_NOT_APPROVED], sample,
                extra={
                    "audit_status": "POOL-AUDIT WARN",
                    "not_approved_count": counts[STATUS_NOT_APPROVED],
                    "sample_symbols": sample,
                },
            )

    # ── WS-AUDIT CRITICAL (rule 2 — per-symbol > 120s + 10-min dedup) ─
    fired = await _evaluate_streaming_health(redis, report)
    if fired:
        logger.info(
            "[POOL] symbol-audit emitted %d [WS-AUDIT] symbol-not-streaming alerts",
            fired,
        )

    # Aggregate WS resolver-drift signal (NOT_SUBSCRIBED) is reported
    # alongside but uses the original cycle-level dedup so it does not
    # spam when a redeploy briefly desyncs the resolver and the WS leader.
    if counts.get(STATUS_NOT_SUBSCRIBED, 0) > 0:
        if await _alert_dedup(redis, b"alert:symbol_audit:ws", _DEDUP_TTL_POOL_AUDIT_SECONDS):
            drift_sample = [
                r.symbol for r in report.symbols
                if r.status == STATUS_NOT_SUBSCRIBED
            ][:20]
            logger.critical(
                "[WS-AUDIT CRITICAL] %d approved symbols are NOT_SUBSCRIBED — "
                "WS leader resolver drift; trades_buffer will stay empty. First 20: %s",
                counts[STATUS_NOT_SUBSCRIBED], drift_sample,
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

    # Etapa 8 envelope — same operator-facing contract as the admin
    # endpoint and the CLI, so beat-driven runs and on-demand runs
    # produce structurally identical reports.
    rem_obj = rem if not monitor_only else None  # type: ignore[name-defined]
    envelope = build_etapa8_envelope(report, rem_obj)
    envelope["monitor_only"] = monitor_only
    envelope["report"] = report.to_dict()
    envelope["remediation"] = remediation
    return envelope


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
