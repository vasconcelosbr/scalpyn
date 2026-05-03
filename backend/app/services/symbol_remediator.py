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
ACTION_SKIP_NOT_TRADABLE: str = "skip_not_tradable_on_gate"


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
    """Set ``is_approved = true`` for every symbol in one statement."""
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
        for rec in report.symbols:
            if rec.status != STATUS_NOT_APPROVED:
                continue
            tradable = await self._validator.is_tradable(rec.symbol)
            if not tradable:
                actions.append(RemediationAction(
                    symbol=rec.symbol,
                    action=ACTION_SKIP_NOT_TRADABLE,
                    reason="symbol not present in Gate /spot/currency_pairs",
                ))
                continue
            if not rec.pool_row_exists and not self._approve_unknown:
                actions.append(RemediationAction(
                    symbol=rec.symbol,
                    action=ACTION_SKIP_NOT_TRADABLE,
                    reason="approve_unknown=False and no pool_coins row exists",
                ))
                continue
            approve_targets.append(rec.symbol)
            actions.append(RemediationAction(
                symbol=rec.symbol,
                action=ACTION_APPROVE,
                reason="NOT_APPROVED → set pool_coins.is_approved = true",
            ))

        # ── ACTION 1: bulk approve ───────────────────────────────────────
        if approve_targets and not dry_run:
            from ..database import AsyncSessionLocal
            try:
                async with AsyncSessionLocal() as db:
                    affected = await _bulk_approve(db, approve_targets)
                logger.info(
                    "[AUDIT-FIX] approved %d/%d symbols (validator_failed=%s)",
                    affected, len(approve_targets), self._validator.last_load_failed,
                )
                for a in actions:
                    if a.action == ACTION_APPROVE and a.symbol in approve_targets:
                        a.executed = True
                        a.extra["bulk_affected"] = affected
            except Exception as exc:
                logger.warning("[AUDIT-FIX] bulk approve failed: %s", exc)
                for a in actions:
                    if a.action == ACTION_APPROVE:
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
        for rec in report.symbols:
            if rec.status != STATUS_NO_REDIS_DATA:
                continue
            actions.append(RemediationAction(
                symbol=rec.symbol,
                action=ACTION_RETRY_BUFFER,
                reason="NO_REDIS_DATA → poll trades_buffer for 3×2s",
            ))
        if not dry_run:
            retry_targets = [r for r in report.symbols if r.status == STATUS_NO_REDIS_DATA]
            for rec in retry_targets:
                count = await _retry_buffer(rec.symbol)
                for a in actions:
                    if a.action == ACTION_RETRY_BUFFER and a.symbol == rec.symbol:
                        a.executed = True
                        a.extra["member_count_after_retry"] = count

        # ── ACTION 4: enqueue recompute_indicators ──────────────────────
        recompute_targets = [r for r in report.symbols if r.status == STATUS_NO_INDICATOR_DATA]
        recompute_enqueued = False
        for rec in recompute_targets:
            actions.append(RemediationAction(
                symbol=rec.symbol,
                action=ACTION_RECOMPUTE_INDICATORS,
                reason="NO_INDICATOR_DATA → enqueue compute_indicators.compute_5m",
            ))
        if recompute_targets and self._recompute_indicators and not dry_run:
            try:
                from ..tasks.celery_app import celery_app
                celery_app.send_task("app.tasks.compute_indicators.compute_5m")
                recompute_enqueued = True
                logger.info(
                    "[INDICATORS] enqueued compute_5m for %d symbols",
                    len(recompute_targets),
                )
                for a in actions:
                    if a.action == ACTION_RECOMPUTE_INDICATORS:
                        a.executed = True
            except Exception as exc:
                logger.warning("[INDICATORS] failed to enqueue compute_5m: %s", exc)
                for a in actions:
                    if a.action == ACTION_RECOMPUTE_INDICATORS:
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
    "ACTION_SKIP_NOT_TRADABLE",
    "RemediationAction",
    "RemediationReport",
    "GateSymbolValidator",
    "SymbolRemediator",
]
