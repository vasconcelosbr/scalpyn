"""Operational snapshot service — eventually-consistent system observability.

Task #225 — Centro Operacional de Observability.

Why a snapshot service
----------------------

Calling ``celery_app.control.inspect()``, ``redis.INFO`` or ``SELECT 1`` inside
a request handler ties the user-facing latency of ``/api/dashboard/*`` to the
slowest dependency.  When Redis goes away the inspect call hangs for the full
``broker_transport_options`` timeout (~5 s default) and every dashboard tab
spins.  Worse, every concurrent dashboard viewer opens its own probe.

The fix is the standard pattern: probe out-of-band, cache the result, serve
the cache.  This module owns:

* one ``asyncio.Task`` per probe family (Celery, Redis, DB pool, latency,
  ingestion freshness, score throughput);
* a thread-safe-ish snapshot store (single writer per snapshot, many readers);
* ring buffers for alert / worker / redis-degradation history;
* an alert engine that re-derives the current alert list from the latest
  snapshot every time ``get_overview()`` is called.

Contract for callers
--------------------

* :func:`get_service` returns the process-wide singleton.
* All getters (``get_overview``, ``get_alerts``, ``get_events``, …) are
  *non-blocking* — they read the cached snapshots and return immediately.
* Snapshots that haven't refreshed yet expose ``status='unknown'`` rather
  than raising — the dashboard is the system that must show the user
  *something* even when everything is broken.
* Every refresher swallows its own exceptions and records them on the
  snapshot's ``error`` field; nothing here ever propagates back into the
  FastAPI lifespan or the request loop.

Eventually-consistent — a snapshot is at most ``interval_seconds`` old plus
the probe's own ``timeout``.  Tune the intervals at the bottom of the
module before raising them; longer intervals tighten the budget on
inspect/INFO calls in exchange for staler data.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Tunables (env-overridable) ──────────────────────────────────────────────
# Each refresher runs every N seconds.  Keep these conservative — every tick
# is a network round-trip to Redis / a Celery inspect / a DB query.
INGESTION_INTERVAL_S = int(os.environ.get("OPS_SNAP_INGESTION_S", 10))
CELERY_INTERVAL_S    = int(os.environ.get("OPS_SNAP_CELERY_S", 15))
REDIS_INTERVAL_S     = int(os.environ.get("OPS_SNAP_REDIS_S", 15))
DB_INTERVAL_S        = int(os.environ.get("OPS_SNAP_DB_S", 30))
SCORE_INTERVAL_S     = int(os.environ.get("OPS_SNAP_SCORE_S", 60))
LATENCY_INTERVAL_S   = int(os.environ.get("OPS_SNAP_LATENCY_S", 60))

# Per-probe timeout budgets — never let a hung dependency keep the loop in.
CELERY_TIMEOUT_S = 2.0
REDIS_TIMEOUT_S  = 1.0
DB_TIMEOUT_S     = 3.0

# Ring-buffer caps — bounded memory.  ~100 entries is enough for the operator
# to scroll through "what just happened" without the snapshot ballooning.
EVENT_RING_SIZE = 100


# ─── Snapshot data model ─────────────────────────────────────────────────────
@dataclass
class Snapshot:
    """One probe family's most recent observation.

    ``status`` is the canonical health classification (``ok``, ``degraded``,
    ``critical``, ``unknown``).  The alert engine re-derives alert codes from
    these snapshots — never store an alert directly here.
    """
    as_of: Optional[datetime] = None
    status: str = "unknown"
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "status": self.status,
            "data": self.data,
            "error": self.error,
        }


@dataclass
class _Event:
    ts: datetime
    code: str
    message: str
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "code": self.code,
            "message": self.message,
            "extra": self.extra,
        }


# ─── The service ─────────────────────────────────────────────────────────────
class OperationalSnapshotService:
    """Singleton — own all background probes; expose cached snapshots."""

    def __init__(self) -> None:
        self.ingestion = Snapshot()
        self.celery    = Snapshot()
        self.redis     = Snapshot()
        self.db        = Snapshot()
        self.score     = Snapshot()
        self.latency   = Snapshot()

        self._tasks: List[asyncio.Task] = []
        self._started: bool = False

        # Ring buffers — operators read these via /events.
        self._alert_history:      Deque[_Event] = deque(maxlen=EVENT_RING_SIZE)
        self._worker_events:      Deque[_Event] = deque(maxlen=EVENT_RING_SIZE)
        self._redis_degradations: Deque[_Event] = deque(maxlen=EVENT_RING_SIZE)

        # Track previously-seen alert codes to push new ones into history
        # only on transition (avoid spamming the same code every tick).
        self._prev_alert_codes: set[str] = set()
        # Track previous Celery worker presence for transition events.
        self._prev_workers: set[str] = set()
        # Track previous Redis status for degradation transition events.
        self._prev_redis_alive: Optional[bool] = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        loop_pairs: list[tuple[Callable[[], Awaitable[None]], int, str]] = [
            (self._refresh_ingestion, INGESTION_INTERVAL_S, "ingestion"),
            (self._refresh_celery,    CELERY_INTERVAL_S,    "celery"),
            (self._refresh_redis,     REDIS_INTERVAL_S,     "redis"),
            (self._refresh_db,        DB_INTERVAL_S,        "db"),
            (self._refresh_score,     SCORE_INTERVAL_S,     "score"),
            (self._refresh_latency,   LATENCY_INTERVAL_S,   "latency"),
        ]
        for fn, interval, name in loop_pairs:
            self._tasks.append(asyncio.create_task(
                self._run_periodically(fn, interval, name),
                name=f"ops-snapshot-{name}",
            ))
        logger.info(
            "[ops-snapshot] started %d refreshers (intervals: ing=%ds cel=%ds "
            "redis=%ds db=%ds score=%ds lat=%ds)",
            len(self._tasks), INGESTION_INTERVAL_S, CELERY_INTERVAL_S,
            REDIS_INTERVAL_S, DB_INTERVAL_S, SCORE_INTERVAL_S, LATENCY_INTERVAL_S,
        )

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        logger.info("[ops-snapshot] stopped")

    async def _run_periodically(
        self,
        fn: Callable[[], Awaitable[None]],
        interval_seconds: int,
        name: str,
    ) -> None:
        # Run once immediately so the dashboard isn't blank for the first
        # ``interval_seconds`` after boot.  Failure of a single iteration
        # never breaks the loop — log + continue.
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("[ops-snapshot:%s] iteration failed: %s", name, exc)
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise

    # ── refreshers ─────────────────────────────────────────────────────────
    async def _refresh_ingestion(self) -> None:
        """OHLCV freshness + per-symbol counts (NOW - MAX(time))."""
        from sqlalchemy import text
        from ..database import AsyncSessionLocal

        sql = text(
            """
            WITH last_c AS (
                SELECT MAX(time) AS last_candle
                FROM ohlcv
                WHERE timeframe = '5m'
            ),
            win AS (
                SELECT COUNT(*)::int               AS rows_window,
                       COUNT(DISTINCT symbol)::int AS distinct_symbols
                FROM ohlcv
                WHERE timeframe = '5m'
                  AND time > NOW() - INTERVAL '15 minutes'
            )
            SELECT win.rows_window, win.distinct_symbols, last_c.last_candle,
                   EXTRACT(EPOCH FROM (NOW() - last_c.last_candle))::float AS delay_seconds
            FROM win, last_c
            """
        )
        try:
            async with AsyncSessionLocal() as db:
                row = (await asyncio.wait_for(db.execute(sql), timeout=DB_TIMEOUT_S)).one()
            delay = float(row.delay_seconds) if row.delay_seconds is not None else None
            # Status thresholds aligned with /api/dashboard/health (10/20 min).
            if delay is None:
                status = "unknown"
            elif delay < 600:
                status = "ok"
            elif delay <= 1200:
                status = "degraded"
            else:
                status = "critical"
            self.ingestion = Snapshot(
                as_of=datetime.now(timezone.utc),
                status=status,
                data={
                    "rows_window": int(row.rows_window or 0),
                    "distinct_symbols": int(row.distinct_symbols or 0),
                    "last_candle": row.last_candle.isoformat() if row.last_candle else None,
                    "delay_seconds": delay,
                },
            )
        except asyncio.TimeoutError:
            self.ingestion = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="degraded",
                error=f"DB query timeout after {DB_TIMEOUT_S}s",
                data=self.ingestion.data,  # keep last-known
            )
        except Exception as exc:
            self.ingestion = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="critical",
                error=f"{type(exc).__name__}: {exc}",
                data=self.ingestion.data,
            )

    async def _refresh_celery(self) -> None:
        """Celery worker / beat presence via inspect (off-loop, 2 s budget)."""
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(_inspect_celery_blocking),
                timeout=CELERY_TIMEOUT_S + 1.0,
            )
        except asyncio.TimeoutError:
            self.celery = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="degraded",
                error=f"Celery inspect timeout after {CELERY_TIMEOUT_S}s",
                data=self.celery.data,
            )
            return
        except Exception as exc:
            self.celery = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="critical",
                error=f"{type(exc).__name__}: {exc}",
                data=self.celery.data,
            )
            return

        workers = sorted(data.get("workers", []))
        active_count = sum(len(v or []) for v in (data.get("active") or {}).values())
        # Status: critical when no worker responds; degraded when inspect
        # returned but reports zero queues; ok otherwise.
        if not workers:
            status = "critical"
        elif data.get("error"):
            status = "degraded"
        else:
            status = "ok"

        # Worker-presence transition events.
        now = datetime.now(timezone.utc)
        cur_set = set(workers)
        for w in cur_set - self._prev_workers:
            self._worker_events.append(_Event(now, "worker_online", f"Worker online: {w}", {"worker": w}))
        for w in self._prev_workers - cur_set:
            self._worker_events.append(_Event(now, "worker_offline", f"Worker offline: {w}", {"worker": w}))
        self._prev_workers = cur_set

        self.celery = Snapshot(
            as_of=now,
            status=status,
            data={
                "workers": workers,
                "worker_count": len(workers),
                "active_tasks": active_count,
                "registered_tasks": data.get("registered_count", 0),
            },
            error=data.get("error"),
        )

    async def _refresh_redis(self) -> None:
        """Redis liveness + key INFO sections (1 s budget)."""
        try:
            from .redis_client import get_async_redis
        except Exception as exc:
            self.redis = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="critical",
                error=f"redis_client import failed: {exc}",
            )
            return

        rc = await get_async_redis()
        now = datetime.now(timezone.utc)
        if rc is None:
            alive = False
            data = {"alive": False}
            err = "Redis client unavailable"
        else:
            try:
                async def _probe() -> Dict[str, Any]:
                    pong = await rc.ping()
                    info_stats   = await rc.info("stats")
                    info_memory  = await rc.info("memory")
                    info_clients = await rc.info("clients")
                    return {
                        "alive": bool(pong),
                        "connected_clients": int(info_clients.get("connected_clients", 0)),
                        "used_memory_human": info_memory.get("used_memory_human", "?"),
                        "used_memory_bytes": int(info_memory.get("used_memory", 0)),
                        "instantaneous_ops_per_sec": int(info_stats.get("instantaneous_ops_per_sec", 0)),
                        "total_commands_processed": int(info_stats.get("total_commands_processed", 0)),
                    }
                data = await asyncio.wait_for(_probe(), timeout=REDIS_TIMEOUT_S)
                alive = bool(data.get("alive"))
                err = None
            except asyncio.TimeoutError:
                alive = False
                data = {"alive": False}
                err = f"Redis probe timeout after {REDIS_TIMEOUT_S}s"
            except Exception as exc:
                alive = False
                data = {"alive": False}
                err = f"{type(exc).__name__}: {exc}"

        # Transition event — only on flip, not on every tick.
        if self._prev_redis_alive is True and not alive:
            self._redis_degradations.append(_Event(
                now, "redis_down", "Redis indisponível",
                {"error": err},
            ))
        elif self._prev_redis_alive is False and alive:
            self._redis_degradations.append(_Event(
                now, "redis_recovered", "Redis recuperado", {},
            ))
        self._prev_redis_alive = alive

        self.redis = Snapshot(
            as_of=now,
            status="ok" if alive else "critical",
            data=data,
            error=err,
        )

    async def _refresh_db(self) -> None:
        """DB pool stats + SELECT 1 timing."""
        try:
            from sqlalchemy import text
            from ..database import AsyncSessionLocal, engine
            t0 = time.perf_counter()
            async with AsyncSessionLocal() as sess:
                await asyncio.wait_for(sess.execute(text("SELECT 1")), timeout=DB_TIMEOUT_S)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            pool = engine.pool
            data = {
                "select1_ms": round(elapsed_ms, 2),
                "pool_size": pool.size(),
                "checked_out": pool.checkedout(),
                "checked_in": pool.checkedin(),
                "overflow": pool.overflow(),
                "status": pool.status(),
            }
            # `invalidated` exists on QueuePool; absent on NullPool.
            inv = getattr(pool, "_invalidate_time", None)
            if inv is not None:
                data["invalidated_at"] = inv
            status = "ok"
            if elapsed_ms > 500:
                status = "degraded"
            if elapsed_ms > 2000:
                status = "critical"
            self.db = Snapshot(as_of=datetime.now(timezone.utc), status=status, data=data)
        except asyncio.TimeoutError:
            self.db = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="critical",
                error=f"SELECT 1 timeout after {DB_TIMEOUT_S}s",
                data=self.db.data,
            )
        except Exception as exc:
            self.db = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="critical",
                error=f"{type(exc).__name__}: {exc}",
                data=self.db.data,
            )

    async def _refresh_score(self) -> None:
        """Throughput / quality / distribution from decisions_log (24 h)."""
        from sqlalchemy import text
        from ..database import AsyncSessionLocal

        sql = text(
            """
            SELECT
                COUNT(*)::int                                                         AS total,
                COALESCE(SUM(CASE WHEN decision='ALLOW' THEN 1 ELSE 0 END), 0)::int  AS allow_count,
                COALESCE(SUM(CASE WHEN decision='BLOCK' THEN 1 ELSE 0 END), 0)::int  AS block_count,
                AVG(score)::float                                                    AS avg_score,
                MIN(score)::float                                                    AS min_score,
                MAX(score)::float                                                    AS max_score,
                MAX(created_at)                                                      AS last_decision
            FROM decisions_log
            WHERE created_at > NOW() - INTERVAL '24 hours'
            LIMIT 1
            """
        )
        try:
            async with AsyncSessionLocal() as db:
                row = (await asyncio.wait_for(db.execute(sql), timeout=DB_TIMEOUT_S)).one()
            now = datetime.now(timezone.utc)
            total = int(row.total or 0)
            last = row.last_decision
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_seconds = (now - last).total_seconds() if last else None
            # Status: critical when no decision in 30 min, degraded after 10 min.
            if total == 0 or age_seconds is None:
                status = "critical"
            elif age_seconds > 1800:
                status = "critical"
            elif age_seconds > 600:
                status = "degraded"
            else:
                status = "ok"
            self.score = Snapshot(
                as_of=now,
                status=status,
                data={
                    "decisions_24h": total,
                    "allow_24h": int(row.allow_count or 0),
                    "block_24h": int(row.block_count or 0),
                    "allow_rate_24h": (int(row.allow_count or 0) / total) if total else 0.0,
                    "avg_score": float(row.avg_score) if row.avg_score is not None else None,
                    "min_score": float(row.min_score) if row.min_score is not None else None,
                    "max_score": float(row.max_score) if row.max_score is not None else None,
                    "last_decision": last.isoformat() if last else None,
                    "last_decision_age_seconds": age_seconds,
                },
            )
        except asyncio.TimeoutError:
            self.score = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="degraded",
                error=f"DB query timeout after {DB_TIMEOUT_S}s",
                data=self.score.data,
            )
        except Exception as exc:
            self.score = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="critical",
                error=f"{type(exc).__name__}: {exc}",
                data=self.score.data,
            )

    async def _refresh_latency(self) -> None:
        """Decision pipeline latency (p50/p95) from decisions_log.latency_ms."""
        from sqlalchemy import text
        from ..database import AsyncSessionLocal

        sql = text(
            """
            SELECT
                percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)::float AS p50,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::float AS p95,
                MAX(latency_ms)::int AS max_latency,
                COUNT(*)::int        AS samples
            FROM decisions_log
            WHERE created_at > NOW() - INTERVAL '1 hour'
              AND latency_ms IS NOT NULL
            LIMIT 1
            """
        )
        try:
            async with AsyncSessionLocal() as db:
                row = (await asyncio.wait_for(db.execute(sql), timeout=DB_TIMEOUT_S)).one()
            samples = int(row.samples or 0)
            p95 = float(row.p95) if row.p95 is not None else None
            if samples == 0:
                status = "unknown"
            elif p95 is not None and p95 > 5000:
                status = "critical"
            elif p95 is not None and p95 > 2000:
                status = "degraded"
            else:
                status = "ok"
            self.latency = Snapshot(
                as_of=datetime.now(timezone.utc),
                status=status,
                data={
                    "p50_ms": float(row.p50) if row.p50 is not None else None,
                    "p95_ms": p95,
                    "max_ms": int(row.max_latency) if row.max_latency is not None else None,
                    "samples_1h": samples,
                },
            )
        except asyncio.TimeoutError:
            self.latency = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="degraded",
                error=f"DB query timeout after {DB_TIMEOUT_S}s",
                data=self.latency.data,
            )
        except Exception as exc:
            self.latency = Snapshot(
                as_of=datetime.now(timezone.utc),
                status="critical",
                error=f"{type(exc).__name__}: {exc}",
                data=self.latency.data,
            )

    # ── alert engine ───────────────────────────────────────────────────────
    def _evaluate_alerts(self) -> List[Dict[str, Any]]:
        """Re-derive alert list from current snapshots.

        Alert codes (stable, contract):
          * ``redis_down``           — Redis ping failed
          * ``worker_offline``       — no Celery worker responded to inspect
          * ``ingestion_stale``      — OHLCV last candle older than 20 min
          * ``no_decisions``         — no decision in last 30 min
          * ``db_slow``              — SELECT 1 > 500 ms
          * ``latency_spike``        — decision p95 > 2 s in last hour
        """
        alerts: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        # Redis
        if self.redis.status == "critical":
            alerts.append({
                "severity": "critical",
                "category": "infra",
                "code": "redis_down",
                "impact": "WS leader election parado, indicators_provider sem cache, throttle desabilitado.",
                "since": self.redis.as_of.isoformat() if self.redis.as_of else None,
                "details": {"error": self.redis.error},
            })

        # Celery worker
        if self.celery.status == "critical":
            alerts.append({
                "severity": "critical",
                "category": "celery",
                "code": "worker_offline",
                "impact": "Nenhum worker responde — pipeline_scan, collect_market_data e execute_buy não rodam.",
                "since": self.celery.as_of.isoformat() if self.celery.as_of else None,
                "details": {"error": self.celery.error},
            })

        # Ingestion freshness
        delay = self.ingestion.data.get("delay_seconds")
        if isinstance(delay, (int, float)):
            if delay > 1200:
                alerts.append({
                    "severity": "critical",
                    "category": "ingest",
                    "code": "ingestion_stale",
                    "impact": "OHLCV não atualiza há mais de 20 min — decisões usam dados velhos.",
                    "since": self.ingestion.as_of.isoformat() if self.ingestion.as_of else None,
                    "details": {"delay_seconds": delay, "last_candle": self.ingestion.data.get("last_candle")},
                })
            elif delay > 600:
                alerts.append({
                    "severity": "warning",
                    "category": "ingest",
                    "code": "ingestion_lagging",
                    "impact": "OHLCV atrasado entre 10 e 20 min — aceitável em catch-up multi-symbol.",
                    "since": self.ingestion.as_of.isoformat() if self.ingestion.as_of else None,
                    "details": {"delay_seconds": delay},
                })

        # Decisions throughput.  Fire on EITHER zero decisions in 24 h (cold
        # start / scoring engine never ran) OR last decision older than 30 min
        # (engine stalled mid-flight).  The two cases cover different failure
        # modes — the first means no rule has ever passed; the second means
        # rules used to pass but stopped.
        last_age = self.score.data.get("last_decision_age_seconds")
        decisions_24h = self.score.data.get("decisions_24h", 0)
        stalled = isinstance(last_age, (int, float)) and last_age > 1800
        cold = self.score.status != "unknown" and (decisions_24h or 0) == 0
        if stalled or cold:
            alerts.append({
                "severity": "critical",
                "category": "score",
                "code": "no_decisions",
                "impact": (
                    "Nenhuma decisão nas últimas 24h — score engine sem dados/regra ou pipeline_scan parado."
                    if cold else
                    "Nenhuma decisão nos últimos 30 min — score engine ou pipeline_scan parado."
                ),
                "since": self.score.as_of.isoformat() if self.score.as_of else None,
                "details": {"age_seconds": last_age, "decisions_24h": decisions_24h},
            })

        # DB latency
        select1 = self.db.data.get("select1_ms")
        if isinstance(select1, (int, float)) and select1 > 500:
            alerts.append({
                "severity": "critical" if select1 > 2000 else "warning",
                "category": "db",
                "code": "db_slow",
                "impact": "Banco lento — handlers HTTP podem timar out no /api/dashboard.",
                "since": self.db.as_of.isoformat() if self.db.as_of else None,
                "details": {"select1_ms": select1, "pool_status": self.db.data.get("status")},
            })

        # Decision-pipeline latency
        p95 = self.latency.data.get("p95_ms")
        if isinstance(p95, (int, float)) and p95 > 2000:
            alerts.append({
                "severity": "critical" if p95 > 5000 else "warning",
                "category": "latency",
                "code": "latency_spike",
                "impact": "Avaliação de score lenta (>2s p95) — possível gargalo em fetch_merged_indicators.",
                "since": self.latency.as_of.isoformat() if self.latency.as_of else None,
                "details": {"p95_ms": p95, "samples_1h": self.latency.data.get("samples_1h")},
            })

        # Push *new* alert codes into history; clear codes that healed.
        cur_codes = {a["code"] for a in alerts}
        for code in cur_codes - self._prev_alert_codes:
            matching = next((a for a in alerts if a["code"] == code), None)
            if matching:
                self._alert_history.append(_Event(
                    now, code,
                    f"[{matching['severity'].upper()}] {matching['impact']}",
                    {"category": matching["category"], "details": matching.get("details", {})},
                ))
        for code in self._prev_alert_codes - cur_codes:
            self._alert_history.append(_Event(
                now, f"{code}_recovered",
                f"Alerta resolvido: {code}", {},
            ))
        self._prev_alert_codes = cur_codes

        return alerts

    # ── public API ─────────────────────────────────────────────────────────
    def get_overview(self) -> Dict[str, Any]:
        alerts = self._evaluate_alerts()
        # Overall status = worst snapshot status, but "critical" alerts force
        # critical even if all underlying snapshots happen to be ok (e.g.
        # newly-discovered alert engine threshold breach).
        ranks = {"unknown": 0, "ok": 1, "degraded": 2, "critical": 3}
        worst = max(
            (s.status for s in (
                self.ingestion, self.celery, self.redis,
                self.db, self.score, self.latency,
            )),
            key=lambda s: ranks.get(s, 0),
        )
        if any(a["severity"] == "critical" for a in alerts):
            worst = "critical"
        elif any(a["severity"] == "warning" for a in alerts) and worst == "ok":
            worst = "degraded"

        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "overall_status": worst,
            "snapshots": {
                "ingestion": self.ingestion.to_dict(),
                "celery":    self.celery.to_dict(),
                "redis":     self.redis.to_dict(),
                "db":        self.db.to_dict(),
                "score":     self.score.to_dict(),
                "latency":   self.latency.to_dict(),
            },
            "alerts": alerts,
            "alert_count": len(alerts),
        }

    def get_alerts(self) -> Dict[str, Any]:
        alerts = self._evaluate_alerts()
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "current": alerts,
            "history": [e.to_dict() for e in reversed(self._alert_history)],
        }

    def get_events(self) -> Dict[str, Any]:
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "alert_history":      [e.to_dict() for e in reversed(self._alert_history)],
            "worker_events":      [e.to_dict() for e in reversed(self._worker_events)],
            "redis_degradations": [e.to_dict() for e in reversed(self._redis_degradations)],
        }


# ─── Celery inspect helper (blocking — runs in a thread) ────────────────────
def _inspect_celery_blocking() -> Dict[str, Any]:
    """Synchronous Celery inspect probe; runs inside ``asyncio.to_thread``.

    Returning a dict instead of mutating a snapshot keeps the threading
    surface tiny (no shared mutable state across the thread boundary).
    """
    try:
        from ..tasks.celery_app import celery_app
        insp = celery_app.control.inspect(timeout=CELERY_TIMEOUT_S)
        active = insp.active() or {}
        registered = insp.registered() or {}
        flat: set[str] = set()
        for names in registered.values():
            for n in names or []:
                flat.add(n)
        return {
            "workers": list(active.keys() or registered.keys() or []),
            "active": active,
            "registered_count": len(flat),
        }
    except Exception as exc:
        return {"workers": [], "active": {}, "registered_count": 0, "error": f"{type(exc).__name__}: {exc}"}


# ─── Singleton accessor ─────────────────────────────────────────────────────
_service: Optional[OperationalSnapshotService] = None


def get_service() -> OperationalSnapshotService:
    global _service
    if _service is None:
        _service = OperationalSnapshotService()
    return _service


async def start_operational_snapshot() -> Optional[Callable[[], Awaitable[None]]]:
    """Lifespan entry-point.  Returns a stop coroutine, or ``None`` on failure."""
    try:
        svc = get_service()
        await svc.start()
        return svc.stop
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[ops-snapshot] failed to start: %s", exc)
        return None


__all__ = [
    "OperationalSnapshotService",
    "Snapshot",
    "get_service",
    "start_operational_snapshot",
]
