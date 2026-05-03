"""Batch remediator for symbols flagged by :mod:`symbol_health_service`.

Maps each :data:`STATUS_*` to a deterministic, idempotent repair action:

    NOT_APPROVED        → bulk UPDATE pool_coins.is_approved = true
                          (gated by external Gate REST validator so we
                          never approve a symbol that does not exist
                          on the exchange)
    NOT_SUBSCRIBED      → request the WS leader to refresh subscriptions
    NO_REDIS_DATA       → wait for the buffer (3 retries × 2 s); if the
                          buffer is still empty after the WS refresh the
                          symbol is parked for the next audit cycle.
    NO_INDICATOR_DATA   → enqueue ``compute_indicators.compute_5m`` so
                          a fresh microstructure row is written ASAP

Every repair is dry-runnable: pass ``dry_run=True`` and the remediator
returns the list of intended actions without executing any of them.

Validator
---------

``GateSymbolValidator`` keeps a process-wide set of "tradable spot
pairs" pulled from ``GET /spot/currency_pairs`` (Gate.io public).  The
set is refreshed on demand whenever it is older than ``cache_ttl``
(default 1 h) so the audit does not hammer Gate's public quota.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import text

from .symbol_health_service import (
    STATUS_NOT_APPROVED,
    STATUS_NOT_SUBSCRIBED,
    STATUS_NO_INDICATOR_DATA,
    STATUS_NO_REDIS_DATA,
    SymbolHealth,
    SymbolHealthReport,
)

logger = logging.getLogger(__name__)


# ── Action types ────────────────────────────────────────────────────────────
ACTION_APPROVE: str = "approve"
ACTION_REFRESH_WS: str = "refresh_ws_subscriptions"
ACTION_RETRY_BUFFER: str = "retry_buffer"
ACTION_RECOMPUTE_INDICATORS: str = "recompute_indicators"
# Etapa 4 of the prompt: when a NOT_APPROVED symbol fails the
# external Gate validator (par sumiu da exchange / nunca foi listado),
# we DELETE the row from pool_coins instead of leaving stale entries
# behind that will keep firing alerts forever.
ACTION_REMOVE_FROM_POOL: str = "remove_from_pool"
# Backwards-compat alias kept for any caller that imported the old
# constant name. New code should use ACTION_REMOVE_FROM_POOL.
ACTION_SKIP_NOT_TRADABLE: str = ACTION_REMOVE_FROM_POOL


@dataclass
class RemediationAction:
    """One repair step proposed (or executed) for one symbol."""

    symbol: str
    action: str
    reason: str
    executed: bool = False
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RemediationReport:
    """Aggregate output of one remediation run."""

    dry_run: bool
    total_actions: int
    counts_by_action: Dict[str, int]
    actions: List[RemediationAction]
    refresh_subscriptions_requested: bool = False
    recompute_enqueued: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "total_actions": self.total_actions,
            "counts_by_action": self.counts_by_action,
            "refresh_subscriptions_requested": self.refresh_subscriptions_requested,
            "recompute_enqueued": self.recompute_enqueued,
            "actions": [a.to_dict() for a in self.actions],
        }


# ── Gate REST validator ─────────────────────────────────────────────────────


class GateSymbolValidator:
    """In-memory cache of "is this symbol tradable on Gate.io spot?".

    The cache is keyed by normalised symbol (``BTC_USDT``) and refreshed
    when older than ``cache_ttl`` seconds.  When the validator cannot
    reach Gate, :meth:`is_tradable` returns ``True`` (fail-open) — the
    audit must not block on a transient Gate outage; the alternative
    (fail-closed) would mass-skip every NOT_APPROVED symbol.
    """

    def __init__(self, cache_ttl: int = 3600) -> None:
        self._cache_ttl = max(60, int(cache_ttl))
        self._tradable: Set[str] = set()
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()
        self._last_load_failed = False

    async def _refresh_if_stale(self) -> None:
        async with self._lock:
            if (
                self._tradable
                and (time.monotonic() - self._loaded_at) < self._cache_ttl
            ):
                return
            from ..exchange_adapters.gate_adapter import GateAdapter
            try:
                adapter = GateAdapter()
                pairs = await adapter.list_spot_pairs()
            except Exception as exc:
                logger.warning(
                    "[REDIS] symbol_remediator: GateSymbolValidator refresh failed: %s",
                    exc,
                )
                self._last_load_failed = True
                return
            tradable: Set[str] = set()
            for p in pairs or []:
                pair_id = p.get("id") if isinstance(p, dict) else None
                status = p.get("trade_status") if isinstance(p, dict) else None
                if not pair_id:
                    continue
                if status and status != "tradable":
                    continue
                tradable.add(GateAdapter._normalize_symbol(pair_id))
            if tradable:
                self._tradable = tradable
                self._loaded_at = time.monotonic()
                self._last_load_failed = False
                logger.info(
                    "[POOL] GateSymbolValidator refreshed: %d tradable spot pairs",
                    len(tradable),
                )

    async def is_tradable(self, symbol: str) -> bool:
        from ..exchange_adapters.gate_adapter import GateAdapter
        await self._refresh_if_stale()
        # Fail-open when the validator could not load any data — caller
        # is informed via ``last_load_failed``.
        if not self._tradable:
            return True
        return GateAdapter._normalize_symbol(symbol) in self._tradable

    @property
    def last_load_failed(self) -> bool:
        return self._last_load_failed


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _bulk_approve(db, symbols: Iterable[str]) -> int:
    """Set ``is_approved = true`` for every symbol in one statement.

    Three guards are baked into the WHERE clause per Etapa 3.1 of the
    prompt — the statement itself enforces the business rule, never the
    caller, so a misuse of this helper still cannot promote a futures or
    inactive row:

    * ``is_active = TRUE``    — never approve a row the operator already
                                disabled.
    * ``is_approved = FALSE`` — strictly idempotent (no UPDATE storm on
                                a re-run).
    * ``pool_id IN (SELECT id FROM pools WHERE market_type = 'spot')`` —
                                **never** flip a futures row, even if a
                                symbol exists in both spot and futures
                                pools simultaneously.
    """
    syms = [s for s in symbols if s]
    if not syms:
        return 0
    res = await db.execute(
        text("""
            UPDATE pool_coins
               SET is_approved = TRUE
             WHERE symbol = ANY(:syms)
               AND is_active = TRUE
               AND is_approved = FALSE
               AND pool_id IN (
                   SELECT id FROM pools WHERE market_type = 'spot'
               )
        """),
        {"syms": syms},
    )
    await db.commit()
    return int(res.rowcount or 0)


async def _verify_approved(db, symbols: Iterable[str]) -> Set[str]:
    """Return the subset of ``symbols`` whose pool_coins row is now approved + active.

    Per-symbol verification gate (Etapa 8 of the prompt). Only symbols
    confirmed as ``is_active=TRUE AND is_approved=TRUE`` post-update may
    be marked ``executed=True`` so the operator-facing ``corrigidos``
    counter is truthful — bulk rowcount alone hides per-row failures
    such as the row being deleted between SELECT and UPDATE, or the
    spot/futures pool guard rejecting the row silently.
    """
    syms = [s for s in symbols if s]
    if not syms:
        return set()
    res = await db.execute(
        text("""
            SELECT symbol FROM pool_coins
             WHERE symbol = ANY(:syms)
               AND is_active = TRUE
               AND is_approved = TRUE
               AND pool_id IN (SELECT id FROM pools WHERE market_type = 'spot')
        """),
        {"syms": syms},
    )
    return {row[0] for row in res.fetchall()}


async def _verify_removed(db, symbols: Iterable[str]) -> Set[str]:
    """Return the subset of ``symbols`` no longer present in any spot pool row.

    Mirror of :func:`_verify_approved` for the DELETE path: a symbol is
    considered remediated only when no spot pool row remains.
    """
    syms = [s for s in symbols if s]
    if not syms:
        return set()
    res = await db.execute(
        text("""
            SELECT symbol FROM pool_coins
             WHERE symbol = ANY(:syms)
               AND pool_id IN (SELECT id FROM pools WHERE market_type = 'spot')
        """),
        {"syms": syms},
    )
    still_present = {row[0] for row in res.fetchall()}
    return {s for s in syms if s not in still_present}


async def _remove_from_pool(db, symbols: Iterable[str]) -> int:
    """Delete pool_coins rows for symbols that no longer exist on Gate.io spot.

    Etapa 4 of the prompt: when the external validator confirms a
    symbol is not present in ``/spot/currency_pairs`` (delisted, never
    existed, or the pool was seeded from a stale source), the row is
    removed from the pool altogether so the next audit cycle does not
    keep flagging it. Restricted to spot pools to mirror :func:`_bulk_approve`.
    """
    syms = [s for s in symbols if s]
    if not syms:
        return 0
    res = await db.execute(
        text("""
            DELETE FROM pool_coins
             WHERE symbol = ANY(:syms)
               AND pool_id IN (
                   SELECT id FROM pools WHERE market_type = 'spot'
               )
        """),
        {"syms": syms},
    )
    await db.commit()
    return int(res.rowcount or 0)


async def _retry_buffer(symbol: str, retries: int = 3, delay: float = 2.0) -> int:
    """Return the buffer ZCARD after up to ``retries`` polls."""
    from ..exchange_adapters.gate_adapter import GateAdapter
    from .redis_client import get_async_redis

    redis = await get_async_redis()
    if redis is None:
        return 0
    key = f"trades_buffer:spot:{GateAdapter._normalize_symbol(symbol)}".encode()
    last = 0
    for attempt in range(max(1, retries)):
        try:
            last = int(await redis.zcard(key) or 0)
        except Exception as exc:
            logger.debug("[REDIS] retry_buffer zcard failed for %s: %s", symbol, exc)
            last = 0
        if last > 0:
            return last
        if attempt < retries - 1:
            await asyncio.sleep(delay)
    return last


# ── Public API ──────────────────────────────────────────────────────────────


class SymbolRemediator:
    """Apply (or simulate) repairs based on a :class:`SymbolHealthReport`."""

    def __init__(
        self,
        validator: Optional[GateSymbolValidator] = None,
        approve_unknown: bool = True,
        recompute_indicators: bool = True,
    ) -> None:
        self._validator = validator or GateSymbolValidator()
        self._approve_unknown = bool(approve_unknown)
        self._recompute_indicators = bool(recompute_indicators)

    async def remediate(
        self,
        report: SymbolHealthReport,
        dry_run: bool = False,
    ) -> RemediationReport:
        actions: List[RemediationAction] = []

        approve_targets: List[str] = []
        remove_targets: List[str] = []
        for rec in report.symbols:
            if rec.status != STATUS_NOT_APPROVED:
                continue
            tradable = await self._validator.is_tradable(rec.symbol)
            if not tradable:
                # Etapa 4 of the prompt: par sumiu da exchange → remove
                # do pool em vez de skip silencioso. Skip-only deixava o
                # mesmo símbolo aparecer em todo ciclo subsequente.
                remove_targets.append(rec.symbol)
                actions.append(RemediationAction(
                    symbol=rec.symbol,
                    action=ACTION_REMOVE_FROM_POOL,
                    reason="symbol not present in Gate /spot/currency_pairs — DELETE from pool_coins",
                ))
                continue
            if not self._approve_unknown:
                actions.append(RemediationAction(
                    symbol=rec.symbol,
                    action=ACTION_APPROVE,
                    reason="NOT_APPROVED — skipped (approve_unknown=False)",
                ))
                continue
            if self._validator.last_load_failed:
                # Fail-closed: never auto-approve while we cannot
                # confirm exchange existence. The validator returned
                # tradable=True only because its cache was empty after
                # a failed refresh; promoting the symbol now would
                # bypass the required external-existence check.
                actions.append(RemediationAction(
                    symbol=rec.symbol,
                    action=ACTION_APPROVE,
                    reason="NOT_APPROVED — skipped (validator_unavailable, fail-closed)",
                    error="validator_unavailable",
                ))
                continue
            approve_targets.append(rec.symbol)
            actions.append(RemediationAction(
                symbol=rec.symbol,
                action=ACTION_APPROVE,
                reason="NOT_APPROVED → set pool_coins.is_approved = true",
            ))

        # ── ACTION 1: bulk approve + per-symbol verification ─────────────
        # Rowcount alone is insufficient — a row may have been deleted
        # between SELECT and UPDATE, rejected by the spot/futures pool
        # guard, or flipped to is_active=false concurrently. Re-query
        # per-row state and only mark executed=True for verified rows.
        approved_verified: Set[str] = set()
        if approve_targets and not dry_run:
            from ..database import AsyncSessionLocal
            try:
                async with AsyncSessionLocal() as db:
                    affected = await _bulk_approve(db, approve_targets)
                    approved_verified = await _verify_approved(db, approve_targets)
                logger.info(
                    "[AUDIT-FIX] approved %d/%d symbols (verified=%d, validator_failed=%s)",
                    affected, len(approve_targets), len(approved_verified),
                    self._validator.last_load_failed,
                )
                for a in actions:
                    if a.action == ACTION_APPROVE and a.symbol in approve_targets:
                        a.extra["bulk_affected"] = affected
                        if a.symbol in approved_verified:
                            a.executed = True
                            logger.info(
                                "[AUDIT-FIX] symbol=%s from=NOT_APPROVED to=APPROVED action=%s",
                                a.symbol, ACTION_APPROVE,
                            )
                        else:
                            a.error = "post-approval verification failed (row missing, futures, or inactive)"
            except Exception as exc:
                logger.warning("[AUDIT-FIX] bulk approve failed: %s", exc)
                for a in actions:
                    if a.action == ACTION_APPROVE:
                        a.error = f"{type(exc).__name__}: {exc}"

        # ── ACTION 1b: bulk remove + per-symbol verification ─────────────
        removed_verified: Set[str] = set()
        if remove_targets and not dry_run:
            from ..database import AsyncSessionLocal
            try:
                async with AsyncSessionLocal() as db:
                    removed = await _remove_from_pool(db, remove_targets)
                    removed_verified = await _verify_removed(db, remove_targets)
                logger.warning(
                    "[AUDIT-FIX] removed %d/%d non-tradable spot symbols (verified=%d)",
                    removed, len(remove_targets), len(removed_verified),
                )
                for a in actions:
                    if a.action == ACTION_REMOVE_FROM_POOL and a.symbol in remove_targets:
                        a.extra["bulk_removed"] = removed
                        if a.symbol in removed_verified:
                            a.executed = True
                            logger.info(
                                "[AUDIT-FIX] symbol=%s from=NOT_APPROVED to=REMOVED action=%s",
                                a.symbol, ACTION_REMOVE_FROM_POOL,
                            )
                        else:
                            a.error = "post-delete verification failed (row still present)"
            except Exception as exc:
                logger.warning("[AUDIT-FIX] bulk remove_from_pool failed: %s", exc)
                for a in actions:
                    if a.action == ACTION_REMOVE_FROM_POOL:
                        a.error = f"{type(exc).__name__}: {exc}"

        # ── ACTION 2: refresh WS subscriptions ──────────────────────────
        # Triggered when we just approved any symbol OR when at least one
        # symbol is NOT_SUBSCRIBED (resolver drift).  One refresh covers
        # every newly-approved symbol — never per-symbol.
        not_subscribed = [r for r in report.symbols if r.status == STATUS_NOT_SUBSCRIBED]
        refresh_requested = False
        need_refresh = bool(approve_targets) or bool(not_subscribed)
        if need_refresh:
            for r in not_subscribed:
                actions.append(RemediationAction(
                    symbol=r.symbol,
                    action=ACTION_REFRESH_WS,
                    reason="NOT_SUBSCRIBED → request WS leader refresh",
                ))
            if not dry_run:
                try:
                    from .gate_ws_leader import refresh_subscriptions
                    res = await refresh_subscriptions()
                    refresh_requested = bool(res.get("requested"))
                    logger.info(
                        "[WS] refresh_subscriptions requested (approved=%d, drift=%d, result=%s)",
                        len(approve_targets), len(not_subscribed), res,
                    )
                    for a in actions:
                        if a.action == ACTION_REFRESH_WS:
                            a.executed = refresh_requested
                            a.extra.update(res)
                except Exception as exc:
                    logger.warning("[WS] refresh_subscriptions failed: %s", exc)
                    for a in actions:
                        if a.action == ACTION_REFRESH_WS:
                            a.error = f"{type(exc).__name__}: {exc}"

        # ── ACTION 3: retry buffer for NO_REDIS_DATA ────────────────────
        # If the symbol is silent AND the validator confirms it is gone
        # from the exchange (delisted), the right fix is to remove it
        # from pool_coins instead of polling forever. Otherwise poll
        # trades_buffer; mark executed=True ONLY on real recovery
        # (ZCARD > 0) so Etapa-8 cannot falsely report "corrigido".
        recovered_after_retry: List[str] = []
        no_redis_delisted: List[str] = []
        no_redis_to_retry: List[str] = []
        for rec in report.symbols:
            if rec.status != STATUS_NO_REDIS_DATA:
                continue
            tradable = await self._validator.is_tradable(rec.symbol)
            if not tradable:
                no_redis_delisted.append(rec.symbol)
                actions.append(RemediationAction(
                    symbol=rec.symbol,
                    action=ACTION_REMOVE_FROM_POOL,
                    reason="NO_REDIS_DATA + symbol absent from Gate /spot/currency_pairs — DELETE from pool_coins",
                ))
                continue
            no_redis_to_retry.append(rec.symbol)
            actions.append(RemediationAction(
                symbol=rec.symbol,
                action=ACTION_RETRY_BUFFER,
                reason="NO_REDIS_DATA → poll trades_buffer for 3×2s",
            ))

        if no_redis_delisted and not dry_run:
            from ..database import AsyncSessionLocal
            try:
                async with AsyncSessionLocal() as db:
                    removed = await _remove_from_pool(db, no_redis_delisted)
                    delisted_verified = await _verify_removed(db, no_redis_delisted)
                logger.warning(
                    "[AUDIT-FIX] removed %d/%d delisted-and-silent symbols (verified=%d)",
                    removed, len(no_redis_delisted), len(delisted_verified),
                )
                for a in actions:
                    if (
                        a.action == ACTION_REMOVE_FROM_POOL
                        and a.symbol in no_redis_delisted
                    ):
                        if a.symbol in delisted_verified:
                            a.executed = True
                            logger.info(
                                "[AUDIT-FIX] symbol=%s from=NO_REDIS_DATA to=REMOVED action=%s",
                                a.symbol, ACTION_REMOVE_FROM_POOL,
                            )
                        else:
                            a.error = "post-delete verification failed (row still present)"
            except Exception as exc:
                logger.warning("[AUDIT-FIX] NO_REDIS_DATA delist removal failed: %s", exc)
                for a in actions:
                    if (
                        a.action == ACTION_REMOVE_FROM_POOL
                        and a.symbol in no_redis_delisted
                    ):
                        a.error = f"{type(exc).__name__}: {exc}"

        if no_redis_to_retry and not dry_run:
            for sym in no_redis_to_retry:
                count = await _retry_buffer(sym)
                recovered = count > 0
                if recovered:
                    recovered_after_retry.append(sym)
                for a in actions:
                    if a.action == ACTION_RETRY_BUFFER and a.symbol == sym:
                        a.extra["member_count_after_retry"] = count
                        # Only count as executed when the buffer truly
                        # came back; otherwise Etapa-8 must keep the
                        # symbol as "pendente".
                        if recovered:
                            a.executed = True
                        else:
                            a.error = "ZCARD still 0 after retry"

        # ── POST-REFRESH ZCARD CONFIRMATION ─────────────────────────────
        # For every symbol whose status depended on a subscription/approval
        # change we just attempted (freshly approved or previously
        # NOT_SUBSCRIBED), we MUST confirm ingestion actually resumed
        # before claiming the fix worked. We poll the trade buffer up to
        # 3×2s (same cadence as ACTION_RETRY_BUFFER). Recompute is then
        # only enqueued for the confirmed set — stale/unconfirmed paths
        # remain "pendente" in Etapa 8.
        confirmed_post_refresh: set = set()
        post_refresh_targets = sorted(
            set(approved_verified)
            | {r.symbol for r in report.symbols if r.status == STATUS_NOT_SUBSCRIBED}
        )
        if post_refresh_targets and not dry_run and refresh_requested:
            for sym in post_refresh_targets:
                count = await _retry_buffer(sym)
                if count > 0:
                    confirmed_post_refresh.add(sym)
            logger.info(
                "[REDIS] post-refresh ZCARD confirmation: %d/%d symbols streaming",
                len(confirmed_post_refresh), len(post_refresh_targets),
            )

        # ── ACTION 4: enqueue recompute_indicators per symbol ───────────
        # Recompute targets are strictly the set with verified ingestion:
        #   * NO_INDICATOR_DATA (already approved+subscribed+streaming —
        #     only indicators are missing).
        #   * NO_REDIS_DATA recovered after the 3×2s retry above.
        #   * Freshly approved + ZCARD>0 confirmed post-refresh.
        #   * Previously NOT_SUBSCRIBED + ZCARD>0 confirmed post-refresh.
        # ``compute_indicators.compute_5m`` iterates the universe so a
        # single dispatch covers every targeted symbol; per-symbol
        # actions are still stamped so the report keeps that intent.
        recompute_targets: List[str] = []
        seen_recompute: set = set()

        def _add_recompute(sym: str, reason: str) -> None:
            if sym in seen_recompute:
                return
            recompute_targets.append(sym)
            seen_recompute.add(sym)
            actions.append(RemediationAction(
                symbol=sym,
                action=ACTION_RECOMPUTE_INDICATORS,
                reason=reason,
            ))

        # Stamp deferred (pendente) actions for symbols that did NOT
        # confirm post-refresh — keeps Etapa-8 honest.
        deferred_post_refresh = set(post_refresh_targets) - confirmed_post_refresh

        for rec in report.symbols:
            if rec.status == STATUS_NO_INDICATOR_DATA:
                _add_recompute(
                    rec.symbol,
                    "NO_INDICATOR_DATA → enqueue compute_indicators.compute_5m",
                )
        for sym in recovered_after_retry:
            _add_recompute(
                sym,
                "NO_REDIS_DATA recovered (ZCARD>0 post-retry) → enqueue compute_5m",
            )
        for sym in sorted(confirmed_post_refresh):
            if sym in approved_verified:
                _add_recompute(
                    sym,
                    "freshly approved + ZCARD>0 post-refresh → enqueue compute_5m",
                )
            else:
                _add_recompute(
                    sym,
                    "NOT_SUBSCRIBED + ZCARD>0 post-refresh → enqueue compute_5m",
                )
        for sym in sorted(deferred_post_refresh):
            actions.append(RemediationAction(
                symbol=sym,
                action=ACTION_RECOMPUTE_INDICATORS,
                reason="ingestion not confirmed (ZCARD=0 after refresh) — deferred",
                executed=False,
                error="ingestion_not_confirmed",
            ))

        recompute_enqueued = False
        if recompute_targets and self._recompute_indicators and not dry_run:
            try:
                # Per-symbol intent is recorded in ``recompute_targets``
                # (one ACTION_RECOMPUTE_INDICATORS row per symbol). The
                # actual dispatch is the existing universe-wide
                # ``compute_5m`` task — there is no per-symbol Celery
                # entrypoint in this codebase, and ``compute_5m``
                # iterates the approved spot universe so every targeted
                # symbol is picked up on the next worker tick. This is
                # the documented contract; if a per-symbol task is ever
                # added, swap the dispatch loop here.
                from ..tasks.celery_app import celery_app
                celery_app.send_task("app.tasks.compute_indicators.compute_5m")
                recompute_enqueued = True
                logger.info(
                    "[INDICATORS] enqueued compute_5m for %d symbols (recovered=%d, no_indicator=%d)",
                    len(recompute_targets),
                    len(recovered_after_retry),
                    len([r for r in report.symbols if r.status == STATUS_NO_INDICATOR_DATA]),
                )
                for a in actions:
                    if (
                        a.action == ACTION_RECOMPUTE_INDICATORS
                        and a.symbol in seen_recompute
                        and a.error != "ingestion_not_confirmed"
                    ):
                        a.executed = True
            except Exception as exc:
                logger.warning("[INDICATORS] failed to enqueue compute_5m: %s", exc)
                for a in actions:
                    if (
                        a.action == ACTION_RECOMPUTE_INDICATORS
                        and a.symbol in seen_recompute
                    ):
                        a.error = f"{type(exc).__name__}: {exc}"

        counts: Dict[str, int] = {}
        for a in actions:
            counts[a.action] = counts.get(a.action, 0) + 1

        return RemediationReport(
            dry_run=dry_run,
            total_actions=len(actions),
            counts_by_action=counts,
            actions=actions,
            refresh_subscriptions_requested=refresh_requested,
            recompute_enqueued=recompute_enqueued,
        )


__all__ = [
    "ACTION_APPROVE",
    "ACTION_REFRESH_WS",
    "ACTION_RETRY_BUFFER",
    "ACTION_RECOMPUTE_INDICATORS",
    "ACTION_REMOVE_FROM_POOL",
    "ACTION_SKIP_NOT_TRADABLE",
    "RemediationAction",
    "RemediationReport",
    "GateSymbolValidator",
    "SymbolRemediator",
    "_bulk_approve",
    "_remove_from_pool",
]
