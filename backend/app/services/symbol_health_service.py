"""Symbol-level ingestion health classifier (Task #194).

Single source of truth for "what is wrong with this symbol's data?".
Classification hierarchy (most blocking first):

    NOT_APPROVED        ``pool_coins.is_approved`` is false (or row missing)
                        for the spot universe.
    NOT_SUBSCRIBED      Approved but absent from the WS leader's spot
                        subscription universe (resolver drift; rare).
    NO_REDIS_DATA       Subscribed but ``trades_buffer:spot:{symbol}`` is
                        empty (Gate WS not flowing or pair untraded).
    NO_INDICATOR_DATA   Buffer present but the latest microstructure row
                        is missing ``taker_ratio``/``volume_delta`` or is
                        older than the staleness threshold.
    OK                  Latest microstructure row carries both
                        ``taker_ratio`` and ``volume_delta`` and is fresh.

The classifier never raises: every probe is wrapped and a missing
subsystem degrades to a status of ``NO_REDIS_DATA`` or ``NO_INDICATOR_DATA``
with the underlying error captured in the ``probe_errors`` field of the
returned :class:`SymbolHealth` record.

Used by:

* ``POST /api/admin/diagnostics/symbol-audit`` (admin batch endpoint)
* ``python -m scripts.symbol_health_audit`` (operator CLI)
* ``app.tasks.symbol_health_audit`` (Celery beat monitor-only mode)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Classification statuses ──────────────────────────────────────────────────
STATUS_NOT_APPROVED: str = "NOT_APPROVED"
# Inactive rows (``pool_coins.is_active = false``) are NOT a pool
# inconsistency — the operator deliberately disabled them. We track
# them as a distinct status so:
#   * the rule-1 monitor "[POOL-AUDIT WARN]" only flags ``is_active=TRUE
#     AND is_approved=FALSE`` (the prompt's exact predicate).
#   * the remediator never tries to "approve" an inactive row (which
#     would silently no-op against the ``is_active=TRUE`` SQL guard and
#     then mark a falsely-corrected action in Etapa 8).
STATUS_INACTIVE: str = "INACTIVE"
STATUS_NOT_SUBSCRIBED: str = "NOT_SUBSCRIBED"
STATUS_NO_REDIS_DATA: str = "NO_REDIS_DATA"
STATUS_NO_INDICATOR_DATA: str = "NO_INDICATOR_DATA"
STATUS_OK: str = "OK"

# Ordered most blocking → least blocking. The classifier returns the
# first status that matches, so this list also encodes the priority.
STATUS_PRIORITY: List[str] = [
    STATUS_NOT_APPROVED,
    STATUS_NOT_SUBSCRIBED,
    STATUS_NO_REDIS_DATA,
    STATUS_NO_INDICATOR_DATA,
    STATUS_INACTIVE,
    STATUS_OK,
]

# A microstructure row older than this is considered stale and the
# symbol is downgraded to NO_INDICATOR_DATA.  The 5m scheduler runs
# every 300s; we tolerate one missed cycle (= 600s) before alerting.
DEFAULT_INDICATOR_MAX_AGE_SECONDS: int = 900
# A trade buffer with members but whose newest entry is older than this
# is considered stale ("WS lost data" or "pair stopped trading").
DEFAULT_BUFFER_NEWEST_MAX_AGE_SECONDS: int = 600


@dataclass
class SymbolHealth:
    """Per-symbol health record.

    Always populated end-to-end so the audit report can render every
    symbol with the same column set even when one probe fails.
    """

    symbol: str
    status: str
    is_approved: bool = False
    pool_row_exists: bool = False
    in_ws_subscription: bool = False
    buffer_member_count: int = 0
    buffer_newest_age_seconds: Optional[float] = None
    indicator_age_seconds: Optional[float] = None
    has_taker_ratio: bool = False
    has_volume_delta: bool = False
    probe_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SymbolHealthReport:
    """Aggregate audit output across many symbols."""

    checked_at: str
    total: int
    counts: Dict[str, int]
    symbols: List[SymbolHealth]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "total": self.total,
            "counts": self.counts,
            "symbols": [s.to_dict() for s in self.symbols],
        }


# ── Probes ──────────────────────────────────────────────────────────────────


async def _load_pool_state(db) -> Dict[str, Dict[str, bool]]:
    """Return ``{normalized_symbol: {is_approved, is_active, exists}}``.

    Loads the full spot pool universe in a single query — the alternative
    (one query per symbol) would multiply the audit cost by N and is
    unnecessary because the table fits comfortably in memory (~10⁴ rows).

    Hardened against ``pool_coins.is_approved`` missing (migration 035 not
    applied): the function falls back to a query without the column and
    treats every active row as approved=False so the classifier reports
    NOT_APPROVED for every symbol — which is the correct behavior because
    nothing can be ingested when the column is missing.
    """
    from ..exchange_adapters.gate_adapter import GateAdapter

    try:
        rows = (await db.execute(text("""
            SELECT pc.symbol, pc.is_active, pc.is_approved
            FROM pool_coins pc
            LEFT JOIN pools p ON p.id = pc.pool_id
            WHERE pc.symbol IS NOT NULL AND pc.symbol <> ''
              AND (p.market_type = 'spot' OR p.market_type IS NULL)
        """))).fetchall()
    except Exception as exc:
        logger.warning("[SYMBOL-HEALTH] is_approved missing — degrading: %s", exc)
        try:
            await db.rollback()
        except Exception:
            pass
        rows = (await db.execute(text("""
            SELECT pc.symbol, pc.is_active
            FROM pool_coins pc
            LEFT JOIN pools p ON p.id = pc.pool_id
            WHERE pc.symbol IS NOT NULL AND pc.symbol <> ''
              AND (p.market_type = 'spot' OR p.market_type IS NULL)
        """))).fetchall()
        return {
            GateAdapter._normalize_symbol(r.symbol): {
                "is_approved": False,
                "is_active": bool(r.is_active),
                "exists": True,
            }
            for r in rows
        }

    out: Dict[str, Dict[str, bool]] = {}
    for r in rows:
        key = GateAdapter._normalize_symbol(r.symbol)
        prev = out.get(key)
        # Multiple memberships → OR the booleans (any approved row wins).
        if prev is None:
            out[key] = {
                "is_approved": bool(r.is_approved),
                "is_active": bool(r.is_active),
                "exists": True,
            }
        else:
            prev["is_approved"] = prev["is_approved"] or bool(r.is_approved)
            prev["is_active"] = prev["is_active"] or bool(r.is_active)
    return out


async def _load_ws_universe() -> set:
    """Snapshot of the WS leader's spot subscription universe."""
    from ..exchange_adapters.gate_adapter import GateAdapter
    from .gate_ws_leader import _resolve_spot_symbols

    try:
        syms = await _resolve_spot_symbols()
    except Exception as exc:
        logger.warning("[SYMBOL-HEALTH] _resolve_spot_symbols failed: %s", exc)
        return set()
    return {GateAdapter._normalize_symbol(s) for s in syms if s}


async def _probe_buffer(redis, symbol: str) -> Dict[str, Any]:
    """Return ``{member_count, newest_age_seconds, error}`` for one buffer."""
    from ..exchange_adapters.gate_adapter import GateAdapter

    out: Dict[str, Any] = {
        "member_count": 0,
        "newest_age_seconds": None,
        "error": None,
    }
    if redis is None:
        out["error"] = "redis_unavailable"
        return out

    key = f"trades_buffer:spot:{GateAdapter._normalize_symbol(symbol)}".encode()
    try:
        count = int(await redis.zcard(key) or 0)
        out["member_count"] = count
        if count > 0:
            newest = await redis.zrange(key, -1, -1, withscores=True)
            if newest:
                now_ms = time.time() * 1000.0
                out["newest_age_seconds"] = round(
                    (now_ms - float(newest[0][1])) / 1000.0, 1
                )
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


async def _probe_latest_indicator(db, symbol: str) -> Dict[str, Any]:
    """Return latest microstructure indicators row keys + age (single-symbol)."""
    out: Dict[str, Any] = {
        "age_seconds": None,
        "has_taker_ratio": False,
        "has_volume_delta": False,
        "error": None,
    }
    try:
        row = (await db.execute(text("""
            SELECT time,
                   (SELECT array_agg(k) FROM jsonb_object_keys(indicators_json) k) AS keys
            FROM indicators
            WHERE symbol = :s
              AND scheduler_group = 'microstructure'
            ORDER BY time DESC
            LIMIT 1
        """), {"s": symbol})).fetchone()
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        try:
            await db.rollback()
        except Exception:
            pass
        return out

    if row is None:
        return out

    keys = list(row.keys or [])
    out["has_taker_ratio"] = "taker_ratio" in keys
    out["has_volume_delta"] = "volume_delta" in keys
    if row.time is not None:
        ts = row.time if row.time.tzinfo else row.time.replace(tzinfo=timezone.utc)
        out["age_seconds"] = round((datetime.now(timezone.utc) - ts).total_seconds(), 1)
    return out


async def _load_latest_indicators(db, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Bulk-load latest microstructure indicator row per symbol in one query.

    Replaces per-symbol :func:`_probe_latest_indicator` calls in the audit
    hot path so the shared :class:`AsyncSession` is never used by more
    than one coroutine at a time (SQLAlchemy ``AsyncSession`` is **not**
    safe for concurrent statement execution — running N gathered probes
    against one session corrupts the connection state).

    Returns ``{symbol: {age_seconds, has_taker_ratio, has_volume_delta,
    error}}`` for every requested symbol; missing rows degrade to the
    empty-record default so the classifier always has the keys it needs.
    """
    default = lambda err=None: {  # noqa: E731
        "age_seconds": None,
        "has_taker_ratio": False,
        "has_volume_delta": False,
        "error": err,
    }
    if not symbols:
        return {}
    try:
        rows = (await db.execute(text("""
            SELECT DISTINCT ON (symbol)
                   symbol, time,
                   (SELECT array_agg(k) FROM jsonb_object_keys(indicators_json) k) AS keys
            FROM indicators
            WHERE symbol = ANY(:syms)
              AND scheduler_group = 'microstructure'
            ORDER BY symbol, time DESC
        """), {"syms": list(symbols)})).fetchall()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            pass
        err = f"{type(exc).__name__}: {exc}"
        return {s: default(err) for s in symbols}

    now = datetime.now(timezone.utc)
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        keys = list(r.keys or [])
        age: Optional[float] = None
        if r.time is not None:
            ts = r.time if r.time.tzinfo else r.time.replace(tzinfo=timezone.utc)
            age = round((now - ts).total_seconds(), 1)
        out[r.symbol] = {
            "age_seconds": age,
            "has_taker_ratio": "taker_ratio" in keys,
            "has_volume_delta": "volume_delta" in keys,
            "error": None,
        }
    for s in symbols:
        out.setdefault(s, default())
    return out


# ── Classifier ──────────────────────────────────────────────────────────────


def _classify(
    symbol: str,
    pool: Dict[str, bool],
    in_ws: bool,
    buf: Dict[str, Any],
    ind: Dict[str, Any],
    indicator_max_age: int,
    buffer_newest_max_age: int,
) -> SymbolHealth:
    errors: List[str] = []
    if buf.get("error"):
        errors.append(f"buffer:{buf['error']}")
    if ind.get("error"):
        errors.append(f"indicator:{ind['error']}")

    raw_is_approved = bool(pool.get("is_approved", False))
    raw_is_active = bool(pool.get("is_active", False))
    is_approved = raw_is_approved and raw_is_active
    pool_exists = bool(pool.get("exists", False))

    record = SymbolHealth(
        symbol=symbol,
        status=STATUS_OK,
        is_approved=is_approved,
        pool_row_exists=pool_exists,
        in_ws_subscription=in_ws,
        buffer_member_count=int(buf.get("member_count") or 0),
        buffer_newest_age_seconds=buf.get("newest_age_seconds"),
        indicator_age_seconds=ind.get("age_seconds"),
        has_taker_ratio=bool(ind.get("has_taker_ratio")),
        has_volume_delta=bool(ind.get("has_volume_delta")),
        probe_errors=errors,
    )

    # Inactive rows: separate status — operator deliberately disabled
    # the row, so this is NOT a pool inconsistency. Keep it visible in
    # the report but excluded from rule-1 alerts and from remediation.
    if pool_exists and not raw_is_active:
        record.status = STATUS_INACTIVE
        return record

    if not is_approved:
        record.status = STATUS_NOT_APPROVED
        return record

    if not in_ws:
        record.status = STATUS_NOT_SUBSCRIBED
        return record

    newest_age = buf.get("newest_age_seconds")
    if (
        record.buffer_member_count == 0
        or newest_age is None
        or newest_age > buffer_newest_max_age
    ):
        record.status = STATUS_NO_REDIS_DATA
        return record

    age = ind.get("age_seconds")
    if (
        not record.has_taker_ratio
        or not record.has_volume_delta
        or age is None
        or age > indicator_max_age
    ):
        record.status = STATUS_NO_INDICATOR_DATA
        return record

    record.status = STATUS_OK
    return record


# ── Public API ──────────────────────────────────────────────────────────────


class SymbolHealthService:
    """Stateless façade — instantiate once per audit run.

    Concurrency on the per-symbol probes is bounded by ``concurrency`` so
    that auditing the full ~10⁴-symbol universe does not melt Redis or
    open one DB connection per symbol.
    """

    def __init__(
        self,
        concurrency: int = 16,
        indicator_max_age_seconds: int = DEFAULT_INDICATOR_MAX_AGE_SECONDS,
        buffer_newest_max_age_seconds: int = DEFAULT_BUFFER_NEWEST_MAX_AGE_SECONDS,
    ) -> None:
        self._concurrency = max(1, int(concurrency))
        self._indicator_max_age = int(indicator_max_age_seconds)
        self._buffer_newest_max_age = int(buffer_newest_max_age_seconds)

    async def audit(
        self,
        symbols: Optional[Iterable[str]] = None,
    ) -> SymbolHealthReport:
        """Run the full audit and return the aggregate report.

        When ``symbols`` is None, every symbol present in ``pool_coins``
        (active OR inactive, approved OR not) is audited so the operator
        sees the complete picture.  When restricted, the universe is the
        union of the requested symbols plus the pool universe — a
        symbol explicitly requested is reported even if it has never
        existed in the pool table.

        Emits the five canonical Etapa 5 logs at the end of every run:

            [POOL] approved symbols: N
            [WS]   subscribed symbols: N
            [WS]   missing symbols: X
            [REDIS] empty buffers: Y
            [INDICATORS] no data: Z
        """
        from ..database import AsyncSessionLocal
        from ..exchange_adapters.gate_adapter import GateAdapter
        from .redis_client import get_async_redis

        redis = await get_async_redis()
        async with AsyncSessionLocal() as db:
            pool_state, ws_universe = await asyncio.gather(
                _load_pool_state(db),
                _load_ws_universe(),
            )

            if symbols is None:
                target = sorted(pool_state.keys())
            else:
                explicit = {GateAdapter._normalize_symbol(s) for s in symbols if s}
                target = sorted(explicit | set(pool_state.keys()))

            # Bulk-load latest indicator row for every symbol *before* fanning
            # out the per-symbol probes — keeps the shared AsyncSession used
            # by exactly one coroutine at a time (SQLAlchemy AsyncSession is
            # not safe for concurrent statement execution).
            indicator_state = await _load_latest_indicators(db, target)

        # Buffer probes hit Redis only — safe to run concurrently. We
        # bound the parallelism so a 10⁴-symbol pool does not melt Redis.
        sem = asyncio.Semaphore(self._concurrency)

        async def _one(sym: str) -> SymbolHealth:
            async with sem:
                pool = pool_state.get(
                    sym,
                    {"is_approved": False, "is_active": False, "exists": False},
                )
                in_ws = sym in ws_universe
                buf = await _probe_buffer(redis, sym)
                ind = indicator_state.get(sym, {
                    "age_seconds": None,
                    "has_taker_ratio": False,
                    "has_volume_delta": False,
                    "error": None,
                })
                return _classify(
                    sym, pool, in_ws, buf, ind,
                    self._indicator_max_age,
                    self._buffer_newest_max_age,
                )

        records = await asyncio.gather(*(_one(s) for s in target))

        counts: Dict[str, int] = {s: 0 for s in STATUS_PRIORITY}
        for r in records:
            counts[r.status] = counts.get(r.status, 0) + 1

        # ── Five canonical Etapa 5 logs ────────────────────────────────
        approved_n = sum(
            1 for r in records if r.is_approved
        )
        subscribed_n = sum(
            1 for r in records if r.is_approved and r.in_ws_subscription
        )
        missing_n = sum(
            1 for r in records if r.is_approved and not r.in_ws_subscription
        )
        logger.info("[POOL] approved symbols: %d", approved_n)
        logger.info("[WS] subscribed symbols: %d", subscribed_n)
        logger.info("[WS] missing symbols: %d", missing_n)
        logger.info("[REDIS] empty buffers: %d", counts.get(STATUS_NO_REDIS_DATA, 0))
        logger.info("[INDICATORS] no data: %d", counts.get(STATUS_NO_INDICATOR_DATA, 0))

        return SymbolHealthReport(
            checked_at=datetime.now(timezone.utc).isoformat(),
            total=len(records),
            counts=counts,
            symbols=records,
        )


# ── Etapa 8 envelope ────────────────────────────────────────────────────
# Portuguese problem labels for the operator-facing report. Matches the
# vocabulary used in the runbook so support staff can copy-paste between
# the JSON output and the runbook checklist.
PROBLEMA_PT: Dict[str, str] = {
    STATUS_NOT_APPROVED: "ativo mas não aprovado em pool_coins",
    STATUS_INACTIVE: "desativado pelo operador (is_active=false)",
    STATUS_NOT_SUBSCRIBED: "aprovado mas WS não subscreveu",
    STATUS_NO_REDIS_DATA: "subscrito mas trades_buffer vazio/stale",
    STATUS_NO_INDICATOR_DATA: "buffer presente mas indicador microstructure ausente/stale",
    STATUS_OK: "—",
}


def build_etapa8_envelope(
    report: SymbolHealthReport,
    remediation: Optional[Any] = None,
) -> Dict[str, Any]:
    """Build the canonical operator-facing report (Etapa 8 of the prompt).

    Output shape — frozen contract, locked in by snapshot test::

        {
          "resumo": {"total": int, "corrigidos": int, "pendentes": int},
          "lista":  [{"symbol", "problema", "ação_aplicada", "status_final"}],
          "system_healthy": bool
        }

    ``ação_aplicada`` and ``status_final`` are derived from the
    :class:`RemediationReport` when one is provided; when ``remediation``
    is ``None`` (audit-only mode) the action defaults to ``"nenhuma"``
    for OK rows and ``"pendente"`` for everything else, and
    ``corrigidos`` stays 0 — the report then represents "what is broken
    right now" rather than "what got fixed".
    """
    by_symbol: Dict[str, "Tuple[str, bool]"] = {}  # noqa: F821
    if remediation is not None:
        for a in getattr(remediation, "actions", []) or []:
            prev = by_symbol.get(a.symbol)
            # Last executed action wins; otherwise first proposed.
            if prev is None or (a.executed and not prev[1]):
                by_symbol[a.symbol] = (a.action, bool(a.executed))

    lista: List[Dict[str, Any]] = []
    corrigidos = 0
    pendentes = 0
    for rec in report.symbols:
        action_pair = by_symbol.get(rec.symbol)
        if action_pair is not None:
            action_str, executed = action_pair
        else:
            action_str = "nenhuma" if rec.status in (STATUS_OK, STATUS_INACTIVE) else "pendente"
            executed = False

        # INACTIVE is "ok by operator intent" — never count it as
        # pendente or corrigido.
        if rec.status in (STATUS_OK, STATUS_INACTIVE):
            status_final = "ok"
        elif executed:
            status_final = "corrigido"
            corrigidos += 1
        else:
            status_final = "pendente"
            pendentes += 1

        lista.append({
            "symbol": rec.symbol,
            "problema": PROBLEMA_PT.get(rec.status, rec.status),
            "ação_aplicada": action_str,
            "status_final": status_final,
        })

    return {
        "resumo": {
            "total": report.total,
            "corrigidos": corrigidos,
            "pendentes": pendentes,
        },
        "lista": lista,
        "system_healthy": pendentes == 0,
    }


__all__ = [
    "STATUS_NOT_APPROVED",
    "STATUS_INACTIVE",
    "STATUS_NOT_SUBSCRIBED",
    "STATUS_NO_REDIS_DATA",
    "STATUS_NO_INDICATOR_DATA",
    "STATUS_OK",
    "STATUS_PRIORITY",
    "DEFAULT_INDICATOR_MAX_AGE_SECONDS",
    "DEFAULT_BUFFER_NEWEST_MAX_AGE_SECONDS",
    "SymbolHealth",
    "SymbolHealthReport",
    "SymbolHealthService",
    "PROBLEMA_PT",
    "build_etapa8_envelope",
    "_classify",
    "_load_pool_state",
    "_load_ws_universe",
    "_load_latest_indicators",
    "_probe_buffer",
    "_probe_latest_indicator",
]
