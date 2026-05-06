"""Operational snapshot service (Task #225).

Probes Celery / Redis / DB / score / ingestion / latencies out-of-band,
caches the results, serves them from ``/api/dashboard/*`` so request
handlers never block on a slow dependency.

Snapshot families (frontend keys off these names — do not rename):
``ingestion``, ``celery``, ``redis``, ``db``, ``score``,
``ingestion_latency``, ``decision_latency``, ``processing_latency``.

A snapshot only flips to ``degraded`` / ``critical`` after three
consecutive failed probes (see ``FAIL_TOLERANCE``); successful probes
reset the streak immediately.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Tunables (env-overridable) ──────────────────────────────────────────────
INGESTION_INTERVAL_S = int(os.environ.get("OPS_SNAP_INGESTION_S", 10))
CELERY_INTERVAL_S    = int(os.environ.get("OPS_SNAP_CELERY_S", 15))
REDIS_INTERVAL_S     = int(os.environ.get("OPS_SNAP_REDIS_S", 15))
DB_INTERVAL_S        = int(os.environ.get("OPS_SNAP_DB_S", 30))
SCORE_INTERVAL_S     = int(os.environ.get("OPS_SNAP_SCORE_S", 60))
LATENCY_INTERVAL_S   = int(os.environ.get("OPS_SNAP_LATENCY_S", 60))
ALERT_INTERVAL_S     = int(os.environ.get("OPS_SNAP_ALERT_S", 5))

# Per-probe timeout budgets — never let a hung dependency keep the loop in.
CELERY_TIMEOUT_S = 2.0
REDIS_TIMEOUT_S  = 1.0
DB_TIMEOUT_S     = 3.0

# Number of consecutive failures before a snapshot is allowed to degrade.
# Single transient failures (network blip, GC pause) are absorbed silently.
FAIL_TOLERANCE = 3

# Ring-buffer caps — bounded memory.
EVENT_RING_SIZE = 100

# Beat-heartbeat thresholds.  Beat ticks once a second and persists its
# schedule; a stale schedule file means the scheduler has been blocked.
BEAT_OK_SECONDS       = 60
BEAT_DEGRADED_SECONDS = 180

# Queue names + the sentinel — backlog probed via Redis ``LLEN``.
_QUEUE_NAMES_DEFAULT = (
    "microstructure",
    "structural",
    "execution",
    "__no_default__",
)


def _queue_names() -> Tuple[str, ...]:
    """Return the queue list, falling back to a constant if Celery imports fail."""
    try:
        from ..tasks.celery_app import ALL_QUEUES  # type: ignore[attr-defined]
        return tuple(ALL_QUEUES) + ("__no_default__",)
    except Exception:
        return _QUEUE_NAMES_DEFAULT


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
    # Consecutive failures since the last successful probe.  Exposed in the
    # serialized form so the dashboard can show "1/3 strikes" while a probe
    # is wobbling.
    failure_streak: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "status": self.status,
            "data": self.data,
            "error": self.error,
            "failure_streak": self.failure_streak,
        }


@dataclass
class _Event:
    ts: datetime
    code: str
    message: str
    extra: Dict[str, Any] = field(default_factory=dict)
    # Category lets ``/events`` filter (alert | worker | redis).
    category: str = "alert"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "code": self.code,
            "message": self.message,
            "extra": self.extra,
            "category": self.category,
        }


# ─── The service ─────────────────────────────────────────────────────────────
class OperationalSnapshotService:
    """Singleton — own all background probes; expose cached snapshots."""

    def __init__(self) -> None:
        self.ingestion         = Snapshot()
        self.celery            = Snapshot()
        self.redis             = Snapshot()
        self.db                = Snapshot()
        self.score             = Snapshot()
        self.ingestion_latency = Snapshot()
        self.decision_latency  = Snapshot()
        self.processing_latency = Snapshot()

        self._tasks: List[asyncio.Task] = []
        self._started: bool = False

        # Ring buffers — operators read these via /events.
        self._alert_history:      Deque[_Event] = deque(maxlen=EVENT_RING_SIZE)
        self._worker_events:      Deque[_Event] = deque(maxlen=EVENT_RING_SIZE)
        self._redis_degradations: Deque[_Event] = deque(maxlen=EVENT_RING_SIZE)

        # Track previously-seen alert codes so we only push transitions.
        self._prev_alert_codes: set[str] = set()
        # First-fire timestamp per code; persisted so `since` is stable.
        self._alert_first_seen: Dict[str, str] = {}
        # Filled by _refresh_alerts so HTTP reads never mutate transitions.
        self._alerts_cache: List[Dict[str, Any]] = []
        self._alerts_cache_as_of: Optional[datetime] = None
        self._prev_workers: set[str] = set()
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
            (self._refresh_alerts,    ALERT_INTERVAL_S,     "alerts"),
        ]
        for fn, interval, name in loop_pairs:
            self._tasks.append(asyncio.create_task(
                self._run_periodically(fn, interval, name),
                name=f"ops-snapshot-{name}",
            ))
        logger.info(
            "[ops-snapshot] started %d refreshers (intervals: ing=%ds cel=%ds "
            "redis=%ds db=%ds score=%ds lat=%ds, fail-tolerance=%d)",
            len(self._tasks), INGESTION_INTERVAL_S, CELERY_INTERVAL_S,
            REDIS_INTERVAL_S, DB_INTERVAL_S, SCORE_INTERVAL_S, LATENCY_INTERVAL_S,
            FAIL_TOLERANCE,
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

    # ── failure-tolerance helper ──────────────────────────────────────────
    def _apply_success(
        self,
        attr: str,
        data: Dict[str, Any],
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Record a successful probe — reset the failure streak immediately."""
        setattr(self, attr, Snapshot(
            as_of=datetime.now(timezone.utc),
            status=status,
            data=data,
            error=error,
            failure_streak=0,
        ))

    def _apply_failure(
        self,
        attr: str,
        error: str,
        soft_status: str = "degraded",
        hard_status: str = "critical",
        down_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a failed probe. Status flips only after FAIL_TOLERANCE
        strikes, but liveness-shaped probes (Redis, Celery) must pass
        ``down_data`` so the UI never reads stale "online" mid-outage.
        """
        prev: Snapshot = getattr(self, attr)
        streak = prev.failure_streak + 1
        if streak < FAIL_TOLERANCE:
            new_status = prev.status if prev.status != "unknown" else soft_status
        else:
            new_status = hard_status
        merged = {**(prev.data or {}), **down_data} if down_data is not None else prev.data
        setattr(self, attr, Snapshot(
            as_of=datetime.now(timezone.utc),
            status=new_status,
            data=merged,
            error=error,
            failure_streak=streak,
        ))

    # ── refreshers ─────────────────────────────────────────────────────────
    async def _refresh_ingestion(self) -> None:
        """OHLCV freshness + per-symbol counts (NOW - MAX(time))."""
        from sqlalchemy import text
        from ..database import AsyncSessionLocal

        # Task #232: ``active_pool_count`` is sampled in the same probe
        # so the alert engine can distinguish "ingestion broken" (pool
        # has work but no candles arrive) from "pool starved" (no
        # active symbol exists, ingestion legitimately idle).
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
            ),
            pool AS (
                SELECT COUNT(DISTINCT symbol)::int AS active_pool_count
                FROM pool_coins
                WHERE is_active = true
            )
            SELECT win.rows_window, win.distinct_symbols, last_c.last_candle,
                   EXTRACT(EPOCH FROM (NOW() - last_c.last_candle))::float AS delay_seconds,
                   pool.active_pool_count
            FROM win, last_c, pool
            """
        )
        try:
            async with AsyncSessionLocal() as db:
                row = (await asyncio.wait_for(db.execute(sql), timeout=DB_TIMEOUT_S)).one()
            delay = float(row.delay_seconds) if row.delay_seconds is not None else None
            active_pool_count = int(row.active_pool_count or 0)
            # Task #232 — explicit pool_state dimension so the alert
            # engine and dashboard can branch on a single, named value
            # instead of inferring intent from (active_pool_count,
            # delay_seconds) tuples:
            #   * OK                 — pool has work AND ingestion fresh
            #   * STARVED_NO_ACTIVE  — zero active symbols, ingestion idle by design
            #   * STALLED            — pool has work but ingestion is late
            if active_pool_count == 0:
                pool_state = "STARVED_NO_ACTIVE"
                status = "ok"
            elif delay is None:
                # Task #232 — active symbols exist but no candle has
                # ever landed in the freshness window: this is a hard
                # ingestion outage, not an "unknown". Treat as STALLED
                # so the alert engine raises ingestion_stale.
                pool_state = "STALLED"
                status = "critical"
            elif delay < 600:
                pool_state = "OK"
                status = "ok"
            elif delay <= 1200:
                pool_state = "STALLED"
                status = "degraded"
            else:
                pool_state = "STALLED"
                status = "critical"
            data = {
                "rows_window": int(row.rows_window or 0),
                "distinct_symbols": int(row.distinct_symbols or 0),
                "last_candle": row.last_candle.isoformat() if row.last_candle else None,
                "delay_seconds": delay,
                "active_pool_count": active_pool_count,
                "pool_state": pool_state,
            }
            self._apply_success("ingestion", data, status)
            # Mirror onto the latency family so /pipeline-latency has a
            # uniform shape across the three latency dimensions.
            lat_status = status if status != "critical" else "critical"
            self._apply_success(
                "ingestion_latency",
                {
                    "delay_seconds": delay,
                    "last_candle": data["last_candle"],
                    "rows_window": data["rows_window"],
                },
                lat_status,
            )
        except asyncio.TimeoutError:
            self._apply_failure("ingestion", f"DB query timeout after {DB_TIMEOUT_S}s")
        except Exception as exc:
            self._apply_failure(
                "ingestion",
                f"{type(exc).__name__}: {exc}",
                hard_status="critical",
            )

    async def _refresh_celery(self) -> None:
        """Celery worker / beat presence + per-queue task breakdown.

        Probe failures (inspect raises, returns ``error``, or yields no
        workers) flow through ``_apply_failure`` so the 3-strike streak
        gates the eventual degradation — a single network blip on the
        broker no longer flips the snapshot to ``critical`` immediately.
        """
        # Liveness-shaped fields written on every failure path so the UI
        # never reads a stale "online" view while status is critical.
        celery_down: Dict[str, Any] = {
            "workers": [],
            "worker_count": 0,
            "active_tasks": 0,
            "reserved_tasks": 0,
            "scheduled_tasks": 0,
            "registered_tasks": 0,
            "per_queue": {},
            "alive": False,
        }
        try:
            # End-to-end budget = CELERY_TIMEOUT_S (kombu inspect + wait_for).
            data = await asyncio.wait_for(
                asyncio.to_thread(_inspect_celery_blocking),
                timeout=CELERY_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            self._apply_failure(
                "celery",
                f"Celery inspect timeout after {CELERY_TIMEOUT_S}s",
                down_data=celery_down,
            )
            return
        except Exception as exc:
            self._apply_failure("celery", f"{type(exc).__name__}: {exc}", down_data=celery_down)
            return

        if data.get("error"):
            self._apply_failure("celery", str(data["error"]), down_data=celery_down)
            return

        workers = sorted(data.get("workers", []))
        if not workers:
            # Route through failure path to honour the 3-strike contract.
            self._apply_failure(
                "celery",
                "No workers responded to inspect()",
                down_data=celery_down,
            )
            return

        per_queue = data.get("per_queue", {})
        active_total = sum(q.get("active", 0) for q in per_queue.values())
        reserved_total = sum(q.get("reserved", 0) for q in per_queue.values())
        scheduled_total = sum(q.get("scheduled", 0) for q in per_queue.values())

        beat_age = _beat_schedule_age_seconds()
        if beat_age is None:
            beat_status = "unknown"
        elif beat_age <= BEAT_OK_SECONDS:
            beat_status = "ok"
        elif beat_age <= BEAT_DEGRADED_SECONDS:
            beat_status = "degraded"
        else:
            beat_status = "critical"

        if beat_status == "critical":
            status = "critical"
        elif beat_status == "degraded":
            status = "degraded"
        else:
            status = "ok"

        # Worker-presence transition events.
        now = datetime.now(timezone.utc)
        cur_set = set(workers)
        for w in cur_set - self._prev_workers:
            self._worker_events.append(_Event(
                now, "worker_online", f"Worker online: {w}",
                {"worker": w}, "worker",
            ))
        for w in self._prev_workers - cur_set:
            self._worker_events.append(_Event(
                now, "worker_offline", f"Worker offline: {w}",
                {"worker": w}, "worker",
            ))
        self._prev_workers = cur_set

        self._apply_success(
            "celery",
            {
                "workers": workers,
                "worker_count": len(workers),
                "active_tasks": active_total,
                "reserved_tasks": reserved_total,
                "scheduled_tasks": scheduled_total,
                "registered_tasks": data.get("registered_count", 0),
                "per_queue": per_queue,
                "beat": {
                    "status": beat_status,
                    "schedule_age_seconds": beat_age,
                },
            },
            status,
            error=data.get("error"),
        )

    async def _refresh_redis(self) -> None:
        """Redis liveness + INFO + per-queue LLEN (1 s budget)."""
        # Liveness-shaped down_data — written on every failure path.
        redis_down: Dict[str, Any] = {
            "alive": False,
            "ping_ms": None,
            "queue_lengths": {q: -1 for q in _queue_names()},
            "backlog_total": 0,
            "unrouted_backlog": 0,
        }
        try:
            from .redis_client import get_async_redis
        except Exception as exc:
            self._apply_failure(
                "redis", f"redis_client import failed: {exc}", down_data=redis_down,
            )
            return

        rc = await get_async_redis()
        if rc is None:
            self._record_redis_transition(False, "Redis client unavailable")
            self._apply_failure("redis", "Redis client unavailable", down_data=redis_down)
            return

        async def _probe() -> Dict[str, Any]:
            t0 = time.perf_counter()
            pong = await rc.ping()
            ping_ms = (time.perf_counter() - t0) * 1000.0
            info_stats   = await rc.info("stats")
            info_memory  = await rc.info("memory")
            info_clients = await rc.info("clients")
            queue_lengths: Dict[str, int] = {}
            for q in _queue_names():
                try:
                    queue_lengths[q] = int(await rc.llen(q))
                except Exception:
                    queue_lengths[q] = -1  # signal "probe failed for this key"
            return {
                "alive": bool(pong),
                "ping_ms": round(ping_ms, 2),
                "connected_clients": int(info_clients.get("connected_clients", 0)),
                "used_memory_human": info_memory.get("used_memory_human", "?"),
                "used_memory_bytes": int(info_memory.get("used_memory", 0)),
                "instantaneous_ops_per_sec": int(info_stats.get("instantaneous_ops_per_sec", 0)),
                "total_commands_processed": int(info_stats.get("total_commands_processed", 0)),
                "queue_lengths": queue_lengths,
            }

        try:
            data = await asyncio.wait_for(_probe(), timeout=REDIS_TIMEOUT_S)
            alive = bool(data.get("alive"))
            self._record_redis_transition(alive, None)
            # __no_default__ backlog = invariant #4 alarm (task escaped TASK_ROUTES).
            no_default = data["queue_lengths"].get("__no_default__", 0)
            backlog_total = sum(
                v for k, v in data["queue_lengths"].items()
                if k != "__no_default__" and v >= 0
            )
            data["backlog_total"] = backlog_total
            data["unrouted_backlog"] = no_default
            status = "ok"
            if data.get("ping_ms", 0) > 100:
                status = "degraded"
            self._apply_success("redis", data, status if alive else "critical")
        except asyncio.TimeoutError:
            self._record_redis_transition(False, f"Redis probe timeout after {REDIS_TIMEOUT_S}s")
            self._apply_failure(
                "redis", f"Redis probe timeout after {REDIS_TIMEOUT_S}s",
                down_data=redis_down,
            )
        except Exception as exc:
            self._record_redis_transition(False, f"{type(exc).__name__}: {exc}")
            self._apply_failure(
                "redis", f"{type(exc).__name__}: {exc}", down_data=redis_down,
            )

    def _record_redis_transition(self, alive: bool, err: Optional[str]) -> None:
        now = datetime.now(timezone.utc)
        if self._prev_redis_alive is True and not alive:
            self._redis_degradations.append(_Event(
                now, "redis_down", "Redis indisponível",
                {"error": err}, "redis",
            ))
        elif self._prev_redis_alive is False and alive:
            self._redis_degradations.append(_Event(
                now, "redis_recovered", "Redis recuperado", {}, "redis",
            ))
        self._prev_redis_alive = alive

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
            inv = getattr(pool, "_invalidate_time", None)
            if inv is not None:
                data["invalidated_at"] = inv
            status = "ok"
            if elapsed_ms > 500:
                status = "degraded"
            if elapsed_ms > 2000:
                status = "critical"
            self._apply_success("db", data, status)
        except asyncio.TimeoutError:
            self._apply_failure("db", f"SELECT 1 timeout after {DB_TIMEOUT_S}s")
        except Exception as exc:
            self._apply_failure("db", f"{type(exc).__name__}: {exc}")

    async def _refresh_score(self) -> None:
        """Score engine telemetry split into three sub-families (24 h window):

        * ``throughput``   — decision counts, ALLOW/BLOCK split, throughput/min,
                             age of last decision.
        * ``quality``      — score moments (avg/min/max/stddev) plus pass-rate
                             across L1/L2/L3.
        * ``distribution`` — score histogram (bucketed 0-20, 20-40, 40-60,
                             60-80, 80-100) so the operator can see where the
                             mass sits without pulling the full ``/decisions``
                             endpoint.

        All three live under ``snapshot.data`` keyed by family so the frontend
        can render three distinct panels off a single payload.
        """
        from sqlalchemy import text
        from ..database import AsyncSessionLocal

        # Defensive bounded sample: cap at 100k rows so a runaway burst
        # cannot blow up the snapshot worker's memory / DB time.
        agg_sql = text(
            """
            WITH sample AS (
                SELECT score, decision, l1_pass, l2_pass, l3_pass, metrics, created_at
                FROM decisions_log
                WHERE created_at > NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC
                LIMIT 100000
            )
            SELECT
                COUNT(*)::int                                                                   AS total,
                COALESCE(SUM(CASE WHEN decision='ALLOW' THEN 1 ELSE 0 END), 0)::int            AS allow_count,
                COALESCE(SUM(CASE WHEN decision='BLOCK' THEN 1 ELSE 0 END), 0)::int            AS block_count,
                AVG(score)::float                                                              AS avg_score,
                MIN(score)::float                                                              AS min_score,
                MAX(score)::float                                                              AS max_score,
                STDDEV_SAMP(score)::float                                                      AS stddev_score,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY score)::float                     AS p50_score,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY score)::float                     AS p95_score,
                COALESCE(SUM(CASE WHEN l1_pass IS TRUE THEN 1 ELSE 0 END), 0)::int            AS l1_pass_count,
                COALESCE(SUM(CASE WHEN l2_pass IS TRUE THEN 1 ELSE 0 END), 0)::int            AS l2_pass_count,
                COALESCE(SUM(CASE WHEN l3_pass IS TRUE THEN 1 ELSE 0 END), 0)::int            AS l3_pass_count,
                AVG(
                    (CASE WHEN l1_pass IS TRUE THEN 1.0 ELSE 0.0 END
                   + CASE WHEN l2_pass IS TRUE THEN 1.0 ELSE 0.0 END
                   + CASE WHEN l3_pass IS TRUE THEN 1.0 ELSE 0.0 END) / 3.0
                )::float                                                                       AS avg_confidence,
                COALESCE(SUM(
                    CASE WHEN metrics ? 'indicators_snapshot'
                         AND EXISTS (
                             SELECT 1 FROM jsonb_each(metrics->'indicators_snapshot') e
                             WHERE (e.value->>'value') IS NULL
                         )
                    THEN 1 ELSE 0 END
                ), 0)::int                                                                     AS missing_count,
                COALESCE(SUM(
                    CASE WHEN metrics ? 'indicators_snapshot'
                         AND EXISTS (
                             SELECT 1 FROM jsonb_each(metrics->'indicators_snapshot') e
                             WHERE (e.value->>'stale')::boolean IS TRUE
                         )
                    THEN 1 ELSE 0 END
                ), 0)::int                                                                     AS stale_count,
                MAX(created_at)                                                                AS last_decision
            FROM sample
            """
        )
        dist_sql = text(
            """
            SELECT bucket, COUNT(*)::int AS count
            FROM (
                SELECT CASE
                    WHEN score < 20  THEN '0-20'
                    WHEN score < 40  THEN '20-40'
                    WHEN score < 60  THEN '40-60'
                    WHEN score < 80  THEN '60-80'
                    ELSE '80-100'
                END AS bucket
                FROM decisions_log
                WHERE created_at > NOW() - INTERVAL '24 hours'
                  AND score IS NOT NULL
                LIMIT 100000
            ) sub
            GROUP BY bucket
            """
        )
        # Time-series throughput: per-minute counts for the last 60 min so
        # the operator can see backlog catch-ups vs steady state.
        series_sql = text(
            """
            SELECT bucket_min, decisions_count, scores_count
            FROM (
                SELECT
                    date_trunc('minute', created_at) AS bucket_min,
                    COUNT(*)::int                    AS decisions_count,
                    COUNT(score)::int                AS scores_count
                FROM decisions_log
                WHERE created_at > NOW() - INTERVAL '60 minutes'
                GROUP BY 1
            ) s
            ORDER BY bucket_min ASC
            LIMIT 60
            """
        )
        try:
            async with AsyncSessionLocal() as db:
                row = (await asyncio.wait_for(db.execute(agg_sql), timeout=DB_TIMEOUT_S)).one()
                dist_rows = (await asyncio.wait_for(db.execute(dist_sql), timeout=DB_TIMEOUT_S)).all()
                series_rows = (await asyncio.wait_for(db.execute(series_sql), timeout=DB_TIMEOUT_S)).all()
            now = datetime.now(timezone.utc)
            total = int(row.total or 0)
            last = row.last_decision
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_seconds = (now - last).total_seconds() if last else None
            if total == 0 or age_seconds is None:
                status = "critical"
            elif age_seconds > 1800:
                status = "critical"
            elif age_seconds > 600:
                status = "degraded"
            else:
                status = "ok"

            allow = int(row.allow_count or 0)
            block = int(row.block_count or 0)
            buckets_keys = ["0-20", "20-40", "40-60", "60-80", "80-100"]
            dist_map = {k: 0 for k in buckets_keys}
            for r in dist_rows:
                if r.bucket in dist_map:
                    dist_map[r.bucket] = int(r.count)

            series = [
                {
                    "ts":               r.bucket_min.replace(tzinfo=timezone.utc).isoformat()
                                        if r.bucket_min.tzinfo is None
                                        else r.bucket_min.isoformat(),
                    "decisions_per_min": int(r.decisions_count),
                    "scores_per_min":    int(r.scores_count),
                }
                for r in series_rows
            ]
            decisions_per_min_now = (
                series[-1]["decisions_per_min"] if series else 0
            )
            scores_per_min_now = series[-1]["scores_per_min"] if series else 0
            missing_count = int(row.missing_count or 0)
            stale_count   = int(row.stale_count or 0)

            data = {
                # Sub-family payloads (referenced by name on the frontend).
                "throughput": {
                    "decisions_24h":             total,
                    "allow_24h":                 allow,
                    "block_24h":                 block,
                    "allow_rate_24h":            (allow / total) if total else 0.0,
                    "decisions_per_min_avg_24h": total / (24.0 * 60.0) if total else 0.0,
                    "decisions_per_min_now":     decisions_per_min_now,
                    "scores_per_min_now":        scores_per_min_now,
                    "series_60m":                series,
                    "last_decision":             last.isoformat() if last else None,
                    "last_decision_age_seconds": age_seconds,
                },
                "quality": {
                    "avg_score":              float(row.avg_score)      if row.avg_score      is not None else None,
                    "min_score":              float(row.min_score)      if row.min_score      is not None else None,
                    "max_score":              float(row.max_score)      if row.max_score      is not None else None,
                    "stddev_score":           float(row.stddev_score)   if row.stddev_score   is not None else None,
                    "avg_confidence":         float(row.avg_confidence) if row.avg_confidence is not None else None,
                    "reject_ratio":           (block / total) if total else 0.0,
                    "missing_indicators_pct": (missing_count / total) if total else 0.0,
                    "stale_indicators_pct":   (stale_count   / total) if total else 0.0,
                    "l1_pass_rate":           (int(row.l1_pass_count or 0) / total) if total else 0.0,
                    "l2_pass_rate":           (int(row.l2_pass_count or 0) / total) if total else 0.0,
                    "l3_pass_rate":           (int(row.l3_pass_count or 0) / total) if total else 0.0,
                },
                "distribution": {
                    "buckets":       [{"bucket": k, "count": dist_map[k]} for k in buckets_keys],
                    "total_scored":  sum(dist_map.values()),
                    "p50_score":     float(row.p50_score) if row.p50_score is not None else None,
                    "p95_score":     float(row.p95_score) if row.p95_score is not None else None,
                },
                # Top-level mirrors kept for the legacy system-status view and
                # the alert engine — never remove without auditing both.
                "decisions_24h":             total,
                "allow_24h":                 allow,
                "block_24h":                 block,
                "allow_rate_24h":            (allow / total) if total else 0.0,
                "avg_score":                 float(row.avg_score) if row.avg_score is not None else None,
                "min_score":                 float(row.min_score) if row.min_score is not None else None,
                "max_score":                 float(row.max_score) if row.max_score is not None else None,
                "last_decision":             last.isoformat() if last else None,
                "last_decision_age_seconds": age_seconds,
            }
            self._apply_success("score", data, status)
        except asyncio.TimeoutError:
            self._apply_failure("score", f"DB query timeout after {DB_TIMEOUT_S}s")
        except Exception as exc:
            self._apply_failure("score", f"{type(exc).__name__}: {exc}")

    async def _refresh_latency(self) -> None:
        """Decision (DB) + processing (Prometheus) latency families.

        Ingestion latency is mirrored from ``_refresh_ingestion`` so this
        method only owns the decision and processing dimensions.
        """
        await self._refresh_decision_latency()
        self._refresh_processing_latency()

    async def _refresh_decision_latency(self) -> None:
        """Decision lag = ``decisions_log.created_at - candle.time`` (24 h).

        For each decision we LATERAL-join the most recent OHLCV 5m candle
        for the same symbol whose ``time`` is at or before the decision —
        that candle is the one the score engine read.  The lag is the
        end-to-end delay from "candle is available" to "decision logged".

        Window: 24 h (operational SLO horizon — short enough to surface
        fresh regressions, long enough to dampen single-decision noise).
        Output is in seconds, but reported in ms for symmetry with the
        other latency families.
        """
        from sqlalchemy import text
        from ..database import AsyncSessionLocal

        sql = text(
            """
            WITH bounded_decisions AS (
                SELECT id, symbol, created_at
                FROM decisions_log
                WHERE created_at > NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC
                LIMIT 20000
            ),
            lags AS (
                SELECT EXTRACT(EPOCH FROM (d.created_at - c.time))::float AS lag_s
                FROM bounded_decisions d
                CROSS JOIN LATERAL (
                    SELECT o.time
                    FROM ohlcv o
                    WHERE o.symbol = d.symbol
                      AND o.timeframe = '5m'
                      AND o.time <= d.created_at
                    ORDER BY o.time DESC
                    LIMIT 1
                ) c
            )
            SELECT
                percentile_cont(0.50) WITHIN GROUP (ORDER BY lag_s)::float AS p50_s,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY lag_s)::float AS p95_s,
                MAX(lag_s)::float    AS max_s,
                COUNT(*)::int        AS samples
            FROM lags
            WHERE lag_s IS NOT NULL AND lag_s >= 0
            """
        )
        try:
            async with AsyncSessionLocal() as db:
                row = (await asyncio.wait_for(db.execute(sql), timeout=DB_TIMEOUT_S)).one()
            samples = int(row.samples or 0)
            p50_ms = (float(row.p50_s) * 1000.0) if row.p50_s is not None else None
            p95_ms = (float(row.p95_s) * 1000.0) if row.p95_s is not None else None
            max_ms = (float(row.max_s) * 1000.0) if row.max_s is not None else None
            # Thresholds: a 5m candle + small processing budget means the
            # decision should land within ~30s of the candle.  Anything
            # over 5 min is a real backlog; over 15 min is critical.
            if samples == 0:
                status = "unknown"
            elif p95_ms is not None and p95_ms > 15 * 60_000:
                status = "critical"
            elif p95_ms is not None and p95_ms > 5 * 60_000:
                status = "degraded"
            else:
                status = "ok"
            self._apply_success("decision_latency", {
                "p50_ms": p50_ms,
                "p95_ms": p95_ms,
                "max_ms": max_ms,
                "samples_24h": samples,
                "formula": "decisions_log.created_at - ohlcv.time (latest <= created_at)",
            }, status)
        except asyncio.TimeoutError:
            self._apply_failure("decision_latency", f"DB query timeout after {DB_TIMEOUT_S}s")
        except Exception as exc:
            self._apply_failure("decision_latency", f"{type(exc).__name__}: {exc}")

    def _refresh_processing_latency(self) -> None:
        """Read indicator computation duration histogram (in-process Prometheus)."""
        try:
            stats = _read_processing_histogram()
        except Exception as exc:
            self._apply_failure("processing_latency", f"prometheus read failed: {exc}")
            return
        if stats is None:
            self._apply_success(
                "processing_latency",
                {"available": False},
                "unknown",
                error="prometheus_client not installed",
            )
            return
        samples = stats["samples"]
        p95 = stats["p95_ms"]
        if samples == 0:
            status = "unknown"
        elif p95 is not None and p95 > 2000:
            status = "critical"
        elif p95 is not None and p95 > 500:
            status = "degraded"
        else:
            status = "ok"
        self._apply_success("processing_latency", stats, status)

    # ── alert engine ───────────────────────────────────────────────────────
    async def _refresh_alerts(self) -> None:
        """Background ticker — recompute alerts every ALERT_INTERVAL_S."""
        alerts = self._evaluate_alerts(record_transitions=True)
        self._alerts_cache = alerts
        self._alerts_cache_as_of = datetime.now(timezone.utc)

    def _evaluate_alerts(self, record_transitions: bool = True) -> List[Dict[str, Any]]:
        """Re-derive alert list from current snapshots.

        Alert codes (stable, contract):
          * ``redis_down``               — Redis ping failed
          * ``worker_offline_60s``       — no Celery worker responded for ≥60 s
          * ``beat_stale_60s``           — Celery Beat schedule file >60 s stale
          * ``queue_backlog_500``        — any non-sentinel queue with >500 pending
          * ``unrouted_backlog``         — tasks stacking on ``__no_default__``
          * ``ingestion_stale``          — OHLCV last candle older than 20 min
          * ``ingestion_lagging``        — OHLCV between 10 and 20 min old
          * ``no_decisions``             — no decision in last 30 min OR 24 h cold
          * ``db_slow``                  — SELECT 1 > 500 ms
          * ``latency_spike``            — candle→decision p95 > 5 min (24h)
          * ``processing_latency_spike`` — indicator compute p95 > 500 ms
        """
        alerts: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        if self.redis.status == "critical":
            alerts.append({
                "severity": "critical", "category": "redis", "code": "redis_down",
                "impact": "WS leader election parado, indicators_provider sem cache, throttle desabilitado.",
                "since": self.redis.as_of.isoformat() if self.redis.as_of else None,
                "details": {"error": self.redis.error},
            })

        # worker_offline_60s — status + streak only (≥4 strikes ≈ 60s at
        # CELERY_INTERVAL_S=15s). Never reads worker_count which can be stale.
        worker_failure_streak = self.celery.failure_streak
        if (
            self.celery.status in ("degraded", "critical")
            and worker_failure_streak >= 4
        ):
            alerts.append({
                "severity": "critical", "category": "celery", "code": "worker_offline_60s",
                "impact": "Nenhum worker responde há >60s — pipeline_scan, collect_market_data e execute_buy parados.",
                "since": self.celery.as_of.isoformat() if self.celery.as_of else None,
                "details": {
                    "error": self.celery.error,
                    "failure_streak": worker_failure_streak,
                    "approx_offline_seconds": worker_failure_streak * CELERY_INTERVAL_S,
                },
            })

        beat = self.celery.data.get("beat") or {}
        beat_status = beat.get("status")
        beat_age = beat.get("schedule_age_seconds")
        # beat_stale_60s — beat heartbeat older than the 60 s OK window.
        if isinstance(beat_age, (int, float)) and beat_age > BEAT_OK_SECONDS:
            alerts.append({
                "severity": "critical" if beat_status == "critical" else "warning",
                "category": "celery", "code": "beat_stale_60s",
                "impact": "Celery Beat travado há >60s — tarefas periódicas (collect, scan, monitor) não estão sendo despachadas.",
                "since": self.celery.as_of.isoformat() if self.celery.as_of else None,
                "details": {"schedule_age_seconds": beat_age},
            })

        # queue_backlog_500 — any non-sentinel queue with > 500 pending tasks.
        queue_lengths = self.redis.data.get("queue_lengths", {}) or {}
        backlogged = {
            q: n for q, n in queue_lengths.items()
            if q != "__no_default__" and isinstance(n, int) and n > 500
        }
        if backlogged:
            worst = max(backlogged.values())
            alerts.append({
                "severity": "critical" if worst > 5000 else "warning",
                "category": "celery", "code": "queue_backlog_500",
                "impact": "Fila Celery acumulada (>500) — workers não estão drenando o broker.",
                "since": self.redis.as_of.isoformat() if self.redis.as_of else None,
                "details": {"queues": backlogged, "worst": worst},
            })

        unrouted = self.redis.data.get("unrouted_backlog", 0)
        if isinstance(unrouted, int) and unrouted > 0:
            alerts.append({
                "severity": "critical", "category": "celery", "code": "unrouted_backlog",
                "impact": "Tarefas escaparam de TASK_ROUTES e estão acumulando na fila sentinela __no_default__ (invariant #4).",
                "since": self.redis.as_of.isoformat() if self.redis.as_of else None,
                "details": {"unrouted_backlog": unrouted},
            })

        delay = self.ingestion.data.get("delay_seconds")
        # Task #232: when the pool has zero active symbols, an "OHLCV
        # is stale" alert is misleading — there is simply nothing to
        # ingest. The ingestion probe stamps ``active_pool_count`` so
        # the alert engine can downgrade ``ingestion_stale`` to a
        # lower-severity ``pool_starved`` notice in that case.
        active_pool_count = self.ingestion.data.get("active_pool_count")
        pool_state = self.ingestion.data.get("pool_state")
        if pool_state == "STARVED_NO_ACTIVE":
            alerts.append({
                "severity": "info", "category": "ingestion", "code": "pool_starved",
                "impact": (
                    "Nenhum símbolo com is_active=true em pool_coins — collector e "
                    "indicadores estão em ciclo vazio por design (Task #232)."
                ),
                "since": self.ingestion.as_of.isoformat() if self.ingestion.as_of else None,
                "details": {
                    "active_pool_count": active_pool_count or 0,
                    "pool_state": pool_state,
                },
            })
        elif isinstance(delay, (int, float)):
            if delay > 1200:
                alerts.append({
                    "severity": "critical", "category": "ingestion", "code": "ingestion_stale",
                    "impact": "OHLCV não atualiza há mais de 20 min — decisões usam dados velhos.",
                    "since": self.ingestion.as_of.isoformat() if self.ingestion.as_of else None,
                    "details": {
                        "delay_seconds": delay,
                        "last_candle": self.ingestion.data.get("last_candle"),
                        "pool_state": pool_state or "STALLED",
                    },
                })
            elif delay > 600:
                alerts.append({
                    "severity": "warning", "category": "ingestion", "code": "ingestion_lagging",
                    "impact": "OHLCV atrasado entre 10 e 20 min — aceitável em catch-up multi-symbol.",
                    "since": self.ingestion.as_of.isoformat() if self.ingestion.as_of else None,
                    "details": {"delay_seconds": delay, "pool_state": pool_state or "OK"},
                })

        last_age = self.score.data.get("last_decision_age_seconds")
        decisions_24h = self.score.data.get("decisions_24h", 0)
        stalled = isinstance(last_age, (int, float)) and last_age > 1800
        cold = self.score.status != "unknown" and (decisions_24h or 0) == 0
        if stalled or cold:
            alerts.append({
                "severity": "critical", "category": "decisions", "code": "no_decisions",
                "impact": (
                    "Nenhuma decisão nas últimas 24h — score engine sem dados/regra ou pipeline_scan parado."
                    if cold else
                    "Nenhuma decisão nos últimos 30 min — score engine ou pipeline_scan parado."
                ),
                "since": self.score.as_of.isoformat() if self.score.as_of else None,
                "details": {"age_seconds": last_age, "decisions_24h": decisions_24h},
            })

        select1 = self.db.data.get("select1_ms")
        if isinstance(select1, (int, float)) and select1 > 500:
            alerts.append({
                "severity": "critical" if select1 > 2000 else "warning",
                "category": "db", "code": "db_slow",
                "impact": "Banco lento — handlers HTTP podem timar out no /api/dashboard.",
                "since": self.db.as_of.isoformat() if self.db.as_of else None,
                "details": {"select1_ms": select1, "pool_status": self.db.data.get("status")},
            })

        p95 = self.decision_latency.data.get("p95_ms")
        if isinstance(p95, (int, float)) and p95 > 5 * 60_000:
            alerts.append({
                "severity": "critical" if p95 > 15 * 60_000 else "warning",
                "category": "latency", "code": "latency_spike",
                "impact": "Lag candle→decisão >5 min (p95) — pipeline_scan ou score engine atrasados.",
                "since": self.decision_latency.as_of.isoformat() if self.decision_latency.as_of else None,
                "details": {"p95_ms": p95, "samples_24h": self.decision_latency.data.get("samples_24h")},
            })

        proc_p95 = self.processing_latency.data.get("p95_ms")
        if isinstance(proc_p95, (int, float)) and proc_p95 > 500:
            alerts.append({
                "severity": "critical" if proc_p95 > 2000 else "warning",
                "category": "latency", "code": "processing_latency_spike",
                "impact": "Cálculo de indicadores lento (>500 ms p95) — gargalo na pipeline microstructure.",
                "since": self.processing_latency.as_of.isoformat() if self.processing_latency.as_of else None,
                "details": {"p95_ms": proc_p95, "samples": self.processing_latency.data.get("samples")},
            })

        # Push new codes / clear healed codes. Only the background ticker
        # mutates state — HTTP reads pass record_transitions=False.
        cur_codes = {a["code"] for a in alerts}
        if record_transitions:
            now_iso = now.isoformat()
            for code in cur_codes - self._prev_alert_codes:
                self._alert_first_seen[code] = now_iso
                matching = next((a for a in alerts if a["code"] == code), None)
                if matching:
                    self._alert_history.append(_Event(
                        now, code,
                        f"[{matching['severity'].upper()}] {matching['impact']}",
                        {"category": matching["category"], "details": matching.get("details", {})},
                        "alert",
                    ))
            for code in self._prev_alert_codes - cur_codes:
                self._alert_first_seen.pop(code, None)
                self._alert_history.append(_Event(
                    now, f"{code}_recovered",
                    f"Alerta resolvido: {code}", {}, "alert",
                ))
            self._prev_alert_codes = cur_codes

        # Stamp `since` with the persisted first-seen so it doesn't drift.
        for a in alerts:
            persisted = self._alert_first_seen.get(a["code"])
            if persisted:
                a["since"] = persisted

        return alerts

    # ── public API ─────────────────────────────────────────────────────────
    def _current_alerts(self) -> List[Dict[str, Any]]:
        """Read from alert cache; fall back to read-only eval on cold boot."""
        if self._alerts_cache_as_of is None:
            return self._evaluate_alerts(record_transitions=False)
        return list(self._alerts_cache)

    def get_overview(self) -> Dict[str, Any]:
        alerts = self._current_alerts()
        ranks = {"unknown": 0, "ok": 1, "degraded": 2, "critical": 3}
        all_snapshots = (
            self.ingestion, self.celery, self.redis, self.db, self.score,
            self.ingestion_latency, self.decision_latency, self.processing_latency,
        )
        worst = max(
            (s.status for s in all_snapshots),
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
                "ingestion":          self.ingestion.to_dict(),
                "celery":             self.celery.to_dict(),
                "redis":              self.redis.to_dict(),
                "db":                 self.db.to_dict(),
                "score":              self.score.to_dict(),
                "ingestion_latency":  self.ingestion_latency.to_dict(),
                "decision_latency":   self.decision_latency.to_dict(),
                "processing_latency": self.processing_latency.to_dict(),
            },
            "alerts": alerts,
            "alert_count": len(alerts),
        }

    def get_alerts(self) -> Dict[str, Any]:
        alerts = self._current_alerts()
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "current": alerts,
            "history": [e.to_dict() for e in reversed(self._alert_history)],
        }

    def get_events(
        self,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Filtered event stream.

        ``category`` ∈ {alert, worker, redis, None}.  ``None`` returns all
        three buckets (legacy contract).  ``limit`` truncates each bucket.
        """
        limit = max(1, min(int(limit or 50), EVENT_RING_SIZE))
        alerts = [e.to_dict() for e in reversed(self._alert_history)][:limit]
        workers = [e.to_dict() for e in reversed(self._worker_events)][:limit]
        redises = [e.to_dict() for e in reversed(self._redis_degradations)][:limit]
        if category in (None, "all", ""):
            return {
                "as_of": datetime.now(timezone.utc).isoformat(),
                "alert_history":      alerts,
                "worker_events":      workers,
                "redis_degradations": redises,
            }
        bucket = {"alert": alerts, "worker": workers, "redis": redises}.get(category, [])
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "events": bucket,
        }

    # ── snapshot-derived view for /api/dashboard/system-status ────────────
    def get_system_status_view(self) -> Dict[str, Any]:
        """Backwards-compatible view used by ``/api/dashboard/system-status``.

        Reads only from in-memory snapshots — never opens a Redis or DB
        connection at request time.  Returns the same field shape as the
        legacy handler so the SystemStatusResponse Pydantic model still
        validates without any frontend changes.
        """
        last_candle = self.ingestion.data.get("last_candle")
        last_decision = self.score.data.get("last_decision")
        return {
            "redis_alive": bool(self.redis.data.get("alive", False)),
            "redis_error": self.redis.error,
            "last_ohlcv_ts": last_candle,
            "last_ohlcv_age_seconds": self.ingestion.data.get("delay_seconds"),
            "last_decision_ts": last_decision,
            "last_decision_age_seconds": self.score.data.get("last_decision_age_seconds"),
            # Heurística: última decisão ≈ última varredura do pipeline.
            "last_pipeline_scan_ts": last_decision,
            "last_pipeline_scan_age_seconds": self.score.data.get("last_decision_age_seconds"),
        }


# ─── Celery inspect helper (blocking — runs in a thread) ────────────────────
def _inspect_celery_blocking() -> Dict[str, Any]:
    """Synchronous Celery probe; runs inside ``asyncio.to_thread``.

    Pulls active/reserved/scheduled and breaks them down per queue using the
    routing_key from each message's ``delivery_info``.  Falls back to a
    bucket-less view if a Celery version doesn't expose delivery_info.
    """
    try:
        from ..tasks.celery_app import celery_app, ALL_QUEUES
        insp = celery_app.control.inspect(timeout=CELERY_TIMEOUT_S)
        active = insp.active() or {}
        reserved = insp.reserved() or {}
        scheduled = insp.scheduled() or {}
        registered = insp.registered() or {}

        flat: set[str] = set()
        for names in registered.values():
            for n in names or []:
                flat.add(n)

        # Per-queue breakdown.  Each task descriptor has
        # ``delivery_info.routing_key`` set to its queue name.
        per_queue: Dict[str, Dict[str, int]] = {
            q: {"active": 0, "reserved": 0, "scheduled": 0} for q in ALL_QUEUES
        }
        per_queue.setdefault("__no_default__", {"active": 0, "reserved": 0, "scheduled": 0})

        def _bucket(items: dict, key: str) -> None:
            for tasks in items.values():
                for t in tasks or []:
                    di = (t.get("delivery_info") if isinstance(t, dict) else None) or {}
                    rk = di.get("routing_key") or di.get("exchange") or "__no_default__"
                    if rk not in per_queue:
                        per_queue[rk] = {"active": 0, "reserved": 0, "scheduled": 0}
                    per_queue[rk][key] += 1

        _bucket(active, "active")
        _bucket(reserved, "reserved")
        _bucket(scheduled, "scheduled")

        workers = sorted(set(active.keys()) | set(registered.keys()))
        return {
            "workers": workers,
            "per_queue": per_queue,
            "registered_count": len(flat),
        }
    except Exception as exc:
        return {
            "workers": [],
            "per_queue": {},
            "registered_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


# ─── Beat heartbeat ─────────────────────────────────────────────────────────
def _beat_schedule_age_seconds() -> Optional[float]:
    """Return seconds since the celerybeat-schedule file was last touched.

    The file is ``backend/celerybeat-schedule`` in the dev workflow and
    ``/var/lib/celerybeat-schedule`` in container deployments.  Beat
    persists schedule state on each tick; a stale mtime means the
    scheduler has been blocked.
    """
    candidates = (
        "backend/celerybeat-schedule",
        "celerybeat-schedule",
        "/var/lib/celerybeat-schedule",
    )
    for p in candidates:
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        return max(0.0, time.time() - mtime)
    return None


# ─── Prometheus histogram reader (in-process) ────────────────────────────────
def _read_processing_histogram() -> Optional[Dict[str, Any]]:
    """Parse ``indicator_computation_duration_seconds`` from the in-process
    Prometheus registry.  Returns ``None`` when prometheus_client is missing.

    Bucket-based p50/p95 estimates use linear interpolation inside the
    matched bucket, which is the standard Prometheus convention.
    """
    try:
        from .robust_indicators.metrics import (  # type: ignore[attr-defined]
            INDICATOR_COMPUTATION_DURATION,
            _PROMETHEUS_AVAILABLE,
        )
    except Exception:
        return None
    if not _PROMETHEUS_AVAILABLE:
        return None

    bucket_totals: Dict[float, float] = {}
    total_count = 0.0
    total_sum = 0.0
    try:
        for metric in INDICATOR_COMPUTATION_DURATION.collect():
            for sample in metric.samples:
                name = sample.name
                if name.endswith("_bucket"):
                    le = sample.labels.get("le")
                    if le is None:
                        continue
                    le_f = float("inf") if le == "+Inf" else float(le)
                    bucket_totals[le_f] = bucket_totals.get(le_f, 0.0) + float(sample.value)
                elif name.endswith("_count"):
                    total_count += float(sample.value)
                elif name.endswith("_sum"):
                    total_sum += float(sample.value)
    except Exception as exc:
        logger.warning("[ops-snapshot] processing histogram read failed: %s", exc)
        return {"available": True, "samples": 0, "error": str(exc),
                "p50_ms": None, "p95_ms": None, "avg_ms": None}

    samples = int(total_count)
    if samples == 0:
        return {"available": True, "samples": 0,
                "p50_ms": None, "p95_ms": None, "avg_ms": None,
                "total_seconds": total_sum}

    def _quantile(q: float) -> Optional[float]:
        if not bucket_totals:
            return None
        sorted_buckets = sorted(bucket_totals.items())
        target = q * samples
        prev_cum = 0.0
        prev_le = 0.0
        for le, cum in sorted_buckets:
            if cum >= target:
                # Linear interp inside the bucket (Prometheus style).
                if le == float("inf") or cum == prev_cum:
                    return le * 1000.0 if le != float("inf") else prev_le * 1000.0
                frac = (target - prev_cum) / (cum - prev_cum)
                return (prev_le + frac * (le - prev_le)) * 1000.0
            prev_cum = cum
            prev_le = le if le != float("inf") else prev_le
        return None

    return {
        "available": True,
        "samples": samples,
        "p50_ms": _quantile(0.50),
        "p95_ms": _quantile(0.95),
        "avg_ms": (total_sum / samples) * 1000.0 if samples else None,
        "total_seconds": total_sum,
    }


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
