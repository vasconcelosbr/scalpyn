"""Celery application configuration for Scalpyn.

Queue topology (Task #216, operator spec parts 4-6):

    microstructure  — 5-minute cadence, latency-tolerant pipeline:
                      collect_5m → compute_5m. Bursty by design (one
                      tick every 5 min).
    structural      — Hourly+ cadence, heavy TA + universe maintenance:
                      collect_all → compute → score, plus discover,
                      fetch_market_caps, macro_regime, simulation,
                      symbol_health_audit, robust_alerts, daily_summary,
                      ohlcv_backfill, and pipeline_scan.scan (the
                      cadence-locked safety-net scan that walks the L1/L2/L3
                      watchlists; per operator spec it stays on the
                      structural queue so a microstructure burst cannot
                      delay the scan and a slow scan cannot starve the
                      5m chain).
    execution       — Latency-sensitive trading critical path:
                      evaluate → execute_buy_cycle, plus anti_liq_monitor.
                      Workers for this queue MUST be deployed isolated
                      from microstructure/structural so a slow indicator
                      compute can never starve a force-close decision.

Architectural invariants enforced at lint level
(``backend/tests/test_celery_routing_invariants.py``):

    1. ``get_merged_indicators`` is the only sanctioned read path for
       indicators inside the four decision tasks.
    2. Each consumer asserts ``is_complete()`` before scoring/decision.
    3. No raw ``send_task()`` / ``apply_async()`` inside ``app/tasks/``
       outside ``task_dispatch.py``.
    4. Every registered task name appears in ``task_routes`` below; an
       unrouted task would otherwise silently land on a non-existent
       fallback queue and never run.
    5. Pool universe queries gate on the right column (Task #232):
       ingestion-side modules (collector, indicators, scoring,
       pipeline_scan funnel) filter on ``is_active = true``; execution
       modules (evaluate_signals, execute_buy) additionally require
       ``is_tradable = true``.

Cost guards live in ``task_annotations`` (Celery's documented mechanism
for applying ``time_limit`` / ``soft_time_limit`` / ``rate_limit`` /
``max_retries`` centrally). This is functionally equivalent to setting
the same fields on each ``@task`` decorator but keeps the policy in one
auditable place. Bounded backoff is implemented per-task where retries
are actually used; ``max_retries`` here is the upper bound.
"""

from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

from ..config import settings

# ── Queue names (single source of truth) ─────────────────────────────────────
QUEUE_MICROSTRUCTURE = "microstructure"
QUEUE_STRUCTURAL = "structural"
QUEUE_EXECUTION = "execution"

ALL_QUEUES = (QUEUE_MICROSTRUCTURE, QUEUE_STRUCTURAL, QUEUE_EXECUTION)

celery_app = Celery(
    "scalpyn_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.collect_market_data",
        "app.tasks.collect_structural_30m",
        "app.tasks.compute_indicators",
        "app.tasks.compute_scores",
        "app.tasks.evaluate_signals",
        "app.tasks.daily_summary",
        "app.tasks.anti_liq_monitor",
        "app.tasks.macro_regime_update",
        "app.tasks.auto_discover_assets",
        "app.tasks.execute_buy",
        "app.tasks.fetch_market_caps",
        "app.tasks.pipeline_scan",
        "app.tasks.ohlcv_backfill",
        "app.tasks.simulation",
        "app.tasks.robust_alerts",
        "app.tasks.symbol_health_audit",
        "app.tasks.decision_log_enricher",
        "app.tasks.trade_reconciliation",
        "app.tasks.trade_monitor",
        "app.tasks.orphan_tx_watchdog",
    ],
)

# ── Task → queue routing (invariant #4) ──────────────────────────────────────
# Every periodic + chained task name is listed explicitly. Adding a new task
# without adding it here causes the lint test
# ``test_every_registered_task_is_routed`` to fail.
TASK_ROUTES = {
    # Microstructure (5-minute cadence chain)
    "app.tasks.collect_market_data.collect_5m":  {"queue": QUEUE_MICROSTRUCTURE},
    "app.tasks.compute_indicators.compute_5m":   {"queue": QUEUE_MICROSTRUCTURE},

    # Structural (hourly+ cadence, heavier work)
    "app.tasks.collect_market_data.collect_all":         {"queue": QUEUE_STRUCTURAL},
    # Task #262 — structural 30m pipeline (replaces OHLCV 1h em collect_all + compute 1h).
    "app.tasks.collect_structural_30m.run":              {"queue": QUEUE_STRUCTURAL},
    "app.tasks.compute_indicators.compute_30m":          {"queue": QUEUE_STRUCTURAL},
    # compute (1h) permanece como stub deprecated — invariant #4 exige
    # rota para toda task registrada. Remover na limpeza pós-estabilização.
    "app.tasks.compute_indicators.compute":              {"queue": QUEUE_STRUCTURAL},
    "app.tasks.compute_scores.score":                    {"queue": QUEUE_STRUCTURAL},
    # pipeline_scan.scan: structural per operator spec (cadence-locked
    # safety-net, must not compete with the bursty 5m chain).
    "app.tasks.pipeline_scan.scan":                      {"queue": QUEUE_STRUCTURAL},
    "app.tasks.auto_discover_assets.discover":           {"queue": QUEUE_STRUCTURAL},
    "app.tasks.fetch_market_caps.fetch_market_caps":     {"queue": QUEUE_STRUCTURAL},
    "app.tasks.macro_regime_update.update":              {"queue": QUEUE_STRUCTURAL},
    "app.tasks.symbol_health_audit.monitor_only":        {"queue": QUEUE_STRUCTURAL},
    "app.tasks.symbol_health_audit.run_repair":          {"queue": QUEUE_STRUCTURAL},
    "app.tasks.simulation.run_simulation_batch":         {"queue": QUEUE_STRUCTURAL},
    "app.tasks.simulation.run_trade_simulation":         {"queue": QUEUE_STRUCTURAL},
    "app.tasks.simulation.get_simulation_stats":         {"queue": QUEUE_STRUCTURAL},
    "app.tasks.robust_alerts.evaluate":                  {"queue": QUEUE_STRUCTURAL},
    "app.tasks.daily_summary.send":                      {"queue": QUEUE_STRUCTURAL},
    "app.tasks.ohlcv_backfill.backfill":                 {"queue": QUEUE_STRUCTURAL},
    "app.tasks.ohlcv_backfill.get_status":               {"queue": QUEUE_STRUCTURAL},

    # Decision Log Enricher (Module 1)
    "app.tasks.decision_log_enricher.enrich":            {"queue": QUEUE_STRUCTURAL},

    # Trade Reconciliation (Module 2)
    "app.tasks.trade_reconciliation.reconcile":          {"queue": QUEUE_STRUCTURAL},

    # Trade Monitor (Module 3)
    "app.tasks.trade_monitor.monitor":                   {"queue": QUEUE_EXECUTION},

    # Orphan Transaction Watchdog (Task #256) — short, infrequent sweep.
    # Lives on the execution queue because the structural worker is the
    # one that historically holds the orphan TX (collect_all/compute), so
    # we want a different worker process tearing it down.
    "app.tasks.orphan_tx_watchdog.kill_orphans":         {"queue": QUEUE_EXECUTION},

    # Execution (latency-sensitive, must run on isolated workers)
    "app.tasks.evaluate_signals.evaluate":          {"queue": QUEUE_EXECUTION},
    "app.tasks.execute_buy.execute_buy_cycle":      {"queue": QUEUE_EXECUTION},
    "app.tasks.anti_liq_monitor.monitor":           {"queue": QUEUE_EXECUTION},
}

# Static queue declarations so beat / dispatch never rely on an "implicit"
# default queue. There is no ``celery`` fallback queue.
#
# The ``__no_default__`` sentinel queue is declared here so kombu's
# ``_create_task_sender`` can resolve ``task_default_queue`` at producer
# construction time (Celery >= 5.6 raises KeyError on every send_task /
# beat tick otherwise — see Task #220). No worker is configured to
# consume from it (workers run with explicit ``--queues=micro,struct,exec``).
# Concretely this means: an unrouted task no longer raises ``NoRoute`` at
# dispatch — instead it accumulates on ``__no_default__`` and surfaces in
# ``/api/system/celery-status`` (visible backlog, no consumer). This
# preserves the loud-failure intent of invariant #4 via observability
# rather than dispatch-time exceptions.
_NO_DEFAULT_QUEUE_NAME = "__no_default__"
TASK_QUEUES = tuple(
    Queue(name, Exchange(name), routing_key=name)
    for name in (*ALL_QUEUES, _NO_DEFAULT_QUEUE_NAME)
)

# ── Per-task cost guards (invariant: no unbounded work) ──────────────────────
# Microstructure: short, predictable, must fit inside the 5-min tick.
# Structural: longer (full universe TA), bounded under 10 min.
# Execution: very short (signal eval / order placement only).
# ``max_retries`` is the upper bound; tasks that should never retry
# (e.g. anti_liq force-close) override locally.
_MICRO_GUARDS = {
    # 2026-05-08 — bumped from 180/150 to 480/420 after collect_5m and
    # compute_5m started timing out silently once the active pool grew to
    # 95 symbols. Combined with ``acks_late=False`` (gotcha #245) the
    # SoftTimeLimitExceeded was killing the task without re-queue, and beat
    # only re-fires every 300 s — result: ohlcv 5m froze for 24h while
    # collect_all (structural, 540s budget) kept persisting 1h candles
    # normally. Kept under structural's 540s ceiling. Pile-up from a slow
    # cycle is bounded by ``acks_late=False`` (orphan tasks are not redelivered).
    "time_limit": 480,
    "soft_time_limit": 420,
    "rate_limit": "12/m",
    "max_retries": 3,
}
_STRUCTURAL_GUARDS = {
    "time_limit": 600,
    "soft_time_limit": 540,
    "rate_limit": "2/m",
    "max_retries": 3,
}
_EXECUTION_GUARDS = {
    "time_limit": 120,
    "soft_time_limit": 100,
    "rate_limit": "4/m",
    "max_retries": 3,
}

# Task #245 — opt-out from the global ``task_acks_late=True`` for tasks that
# are (a) driven by beat on a fixed cadence and (b) idempotent across runs.
# With acks_late=True + task_reject_on_worker_lost=True, hitting ``time_limit``
# (SIGKILL) causes the broker to RE-DELIVER the task — outside the
# ``max_retries`` budget (which only counts explicit ``task.retry()`` calls).
# That's how the structural / microstructure queues built up 1k+ msg backlogs
# in May-2026: a single contended UPSERT past asyncpg's ``command_timeout``
# poisoned the outer transaction, the task raised, the broker requeued it,
# next worker picked it up, hit the same contention, repeat forever.
#
# Setting acks_late=False here means: the task is acknowledged on RECEIPT,
# not on completion. If the worker crashes mid-execution the task is lost
# — but beat re-schedules within seconds (collect_5m: 5 min, collect_all:
# 60 s, compute_*: chained or beat-driven) so a missed cycle is recoverable
# by the next tick. Critical-path execution tasks (evaluate_signals,
# execute_buy_cycle) keep the global acks_late=True — losing a buy decision
# without retry is unacceptable.
_NO_REQUEUE_ON_WORKER_LOSS = {"acks_late": False}

TASK_ANNOTATIONS = {
    # Microstructure (Task #245: idempotent + beat-driven → opt-out of acks_late)
    "app.tasks.collect_market_data.collect_5m":  {**_MICRO_GUARDS, **_NO_REQUEUE_ON_WORKER_LOSS},
    "app.tasks.compute_indicators.compute_5m":   {**_MICRO_GUARDS, **_NO_REQUEUE_ON_WORKER_LOSS},

    # Structural — most tasks (Task #245: idempotent + beat-driven → opt-out of acks_late)
    "app.tasks.collect_market_data.collect_all":         {**_STRUCTURAL_GUARDS, **_NO_REQUEUE_ON_WORKER_LOSS},
    # Task #262 — structural 30m pipeline. rate_limit="2/h" alinha com
    # crontab(minute="0,30"); idempotente + beat-driven → opt-out acks_late.
    "app.tasks.collect_structural_30m.run":              {**_STRUCTURAL_GUARDS, "rate_limit": "2/h", **_NO_REQUEUE_ON_WORKER_LOSS},
    "app.tasks.compute_indicators.compute_30m":          {**_STRUCTURAL_GUARDS, "rate_limit": "2/h", **_NO_REQUEUE_ON_WORKER_LOSS},
    "app.tasks.compute_indicators.compute":              {**_STRUCTURAL_GUARDS, **_NO_REQUEUE_ON_WORKER_LOSS},
    "app.tasks.compute_scores.score":                    {**_STRUCTURAL_GUARDS, **_NO_REQUEUE_ON_WORKER_LOSS},
    # pipeline_scan.scan: structural cadence (5-min safety-net scan,
    # but heavier than the 5m TA chain — uses structural cost guards).
    "app.tasks.pipeline_scan.scan":                      {**_STRUCTURAL_GUARDS, **_NO_REQUEUE_ON_WORKER_LOSS},
    "app.tasks.auto_discover_assets.discover":           {**_STRUCTURAL_GUARDS, "rate_limit": "2/h"},
    "app.tasks.fetch_market_caps.fetch_market_caps":     {**_STRUCTURAL_GUARDS, "rate_limit": "4/h"},
    "app.tasks.macro_regime_update.update":              {**_STRUCTURAL_GUARDS, "rate_limit": "4/h"},
    "app.tasks.symbol_health_audit.monitor_only":        {**_STRUCTURAL_GUARDS, "rate_limit": "12/h"},
    "app.tasks.symbol_health_audit.run_repair":          {**_STRUCTURAL_GUARDS, "rate_limit": "6/h"},
    "app.tasks.simulation.run_simulation_batch":         {**_STRUCTURAL_GUARDS, "rate_limit": "6/h"},
    "app.tasks.simulation.run_trade_simulation":         {**_STRUCTURAL_GUARDS, "rate_limit": "60/m"},
    "app.tasks.simulation.get_simulation_stats":         {"time_limit": 60, "soft_time_limit": 50, "rate_limit": "6/m", "max_retries": 3},
    "app.tasks.robust_alerts.evaluate":                  {"time_limit": 60, "soft_time_limit": 50, "rate_limit": "1/m", "max_retries": 3},
    "app.tasks.daily_summary.send":                      {**_STRUCTURAL_GUARDS, "rate_limit": "1/h"},
    "app.tasks.ohlcv_backfill.backfill":                 {"time_limit": 1800, "soft_time_limit": 1700, "rate_limit": "2/h", "max_retries": 3},
    "app.tasks.ohlcv_backfill.get_status":               {"time_limit": 60, "soft_time_limit": 50, "rate_limit": "6/m", "max_retries": 3},

    # Decision Log Enricher (Module 1)
    "app.tasks.decision_log_enricher.enrich":            {**_STRUCTURAL_GUARDS, "rate_limit": "6/m"},

    # Trade Reconciliation (Module 2) — runs every 60 s, bounded under 2 min
    "app.tasks.trade_reconciliation.reconcile":          {**_STRUCTURAL_GUARDS, "rate_limit": "6/m"},

    # Trade Monitor (Module 3) — runs every 10 s, must be fast; never retry
    # (duplicate close attempts would re-close already-closed trades).
    "app.tasks.trade_monitor.monitor":                   {**_EXECUTION_GUARDS, "max_retries": 0},

    # Orphan TX Watchdog (Task #256) — beat-driven idempotent sweep, opt-out
    # of acks_late so a SIGKILL mid-scan never re-queues. Scans are cheap
    # but the kill statement can wait on Cloud SQL roundtrip; cap at 60s.
    "app.tasks.orphan_tx_watchdog.kill_orphans": {
        "time_limit": 60,
        "soft_time_limit": 50,
        "rate_limit": "1/m",
        "max_retries": 0,
        **_NO_REQUEUE_ON_WORKER_LOSS,
    },

    # Execution
    "app.tasks.evaluate_signals.evaluate":          {**_EXECUTION_GUARDS, "rate_limit": "2/m"},
    "app.tasks.execute_buy.execute_buy_cycle":      {**_EXECUTION_GUARDS, "rate_limit": "2/m"},
    # anti_liq force-close: never retry — duplicate close attempts are dangerous.
    "app.tasks.anti_liq_monitor.monitor":           {**_EXECUTION_GUARDS, "max_retries": 0},
}

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # ── Result storage: disabled globally to prevent Redis OOM ───────────
    # Tasks write their output directly to the DB; no caller needs results.
    task_ignore_result=True,

    # ── Redis connection resilience ──────────────────────────────────────
    broker_pool_limit=2,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,
    broker_connection_retry=True,
    broker_transport_options={
        "max_connections": 4,
        "socket_connect_timeout": 5,
        "socket_timeout": 10,
        "retry_on_timeout": True,
    },
    result_backend_transport_options={
        "max_connections": 2,
    },

    # ── Task execution guards ────────────────────────────────────────────
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_max_tasks_per_child=100,
    result_expires=60,

    # ── Queue topology (no fallback queue — invariant #4) ────────────────
    # Operator spec part 4: an unrouted task MUST fail loudly, not silently
    # land on a default queue. Setting the defaults to a never-declared
    # sentinel (``__no_default__``) means any task that escapes
    # ``TASK_ROUTES`` raises ``NoRoute``/``UndeliverableTask`` at dispatch
    # time and is loud in logs (caught by the lint test
    # ``test_every_registered_task_is_routed`` long before runtime).
    task_queues=TASK_QUEUES,
    task_routes=TASK_ROUTES,
    task_annotations=TASK_ANNOTATIONS,
    task_default_queue="__no_default__",
    task_default_exchange="__no_default__",
    task_default_routing_key="__no_default__",
    task_create_missing_queues=False,
)

# Periodic task schedule
celery_app.conf.beat_schedule = {
    # Collect market data every 60 seconds
    "collect_market_data_every_minute": {
        "task": "app.tasks.collect_market_data.collect_all",
        "schedule": 60.0,
    },
    # Daily summary at 20:00 UTC
    "daily_summary": {
        "task": "app.tasks.daily_summary.send",
        "schedule": crontab(hour=20, minute=0),
    },
    # Anti-liquidation monitor every 2 minutes (was 30s — too frequent)
    "anti_liq_monitor": {
        "task": "app.tasks.anti_liq_monitor.monitor",
        "schedule": 120.0,
    },
    # Macro regime update every 30 minutes
    "macro_regime_update": {
        "task": "app.tasks.macro_regime_update.update",
        "schedule": 1800.0,
    },
    # Auto-discover assets every hour
    "auto_discover_assets_hourly": {
        "task": "app.tasks.auto_discover_assets.discover",
        "schedule": 3600.0,
    },
    # Buy execution cycle every 60 seconds
    "execute_buy_cycle": {
        "task": "app.tasks.execute_buy.execute_buy_cycle",
        "schedule": 60.0,
    },
    # Fetch market caps every 30 minutes
    "fetch_market_caps": {
        "task": "app.tasks.fetch_market_caps.fetch_market_caps",
        "schedule": 1800.0,
    },
    # 5m pipeline: collect 5m candles -> compute 5m indicators
    "collect_5m_data_every_5min": {
        "task": "app.tasks.collect_market_data.collect_5m",
        "schedule": 300.0,
    },
    # Pipeline scan safety-net every 5 minutes
    "pipeline_scan": {
        "task": "app.tasks.pipeline_scan.scan",
        "schedule": 300.0,
    },
    # Run simulation batch every 10 minutes
    "run_simulation_batch_every_10min": {
        "task": "app.tasks.simulation.run_simulation_batch",
        "schedule": crontab(minute="*/10"),
        "kwargs": {
            "limit": 200,
            "skip_existing": True,
        },
    },
    # Robust-indicator alert evaluator every 90 seconds.
    "robust_indicator_alerts": {
        "task": "app.tasks.robust_alerts.evaluate",
        "schedule": 90.0,
    },
    # Symbol ingestion audit (Task #194) every 5 minutes — strictly
    # monitor-only. Active remediation is exposed only via the admin
    # endpoint, the CLI, or the on-demand ``run_repair`` task.
    "symbol_health_audit_monitor_only": {
        "task": "app.tasks.symbol_health_audit.monitor_only",
        "schedule": 300.0,
    },
    # Decision Log Enricher: run every 5 minutes to pick up new ALLOW decisions.
    "decision_log_enricher": {
        "task": "app.tasks.decision_log_enricher.enrich",
        "schedule": 300.0,
    },
    # Trade Reconciliation: run every 60 seconds to detect real Gate fills.
    "trade_reconciliation": {
        "task": "app.tasks.trade_reconciliation.reconcile",
        "schedule": 60.0,
    },
    # Trade Monitor: run every 10 seconds to close TP / SL / timeout trades.
    "trade_monitor": {
        "task": "app.tasks.trade_monitor.monitor",
        "schedule": 10.0,
    },
    # Orphan TX Watchdog (Task #256): every 5 min, kills xact_age > 15min.
    "orphan_tx_watchdog_every_5min": {
        "task": "app.tasks.orphan_tx_watchdog.kill_orphans",
        "schedule": 300.0,
    },
    # Task #262 — Structural 30m collector. Dispara exatamente no
    # fechamento da candle 30m (UTC 00:00, 00:30, …, 23:30) — sem drift
    # de sleep(). Chain: collect_structural_30m → compute_30m → score → evaluate.
    # compute_30m NÃO tem entrada beat — é sempre via chain (invariante:
    # beat só agenda collectors).
    "collect_structural_30m_candle_close": {
        "task": "app.tasks.collect_structural_30m.run",
        "schedule": crontab(minute="0,30"),
    },
}

# ── Wire dedup-release signal (Task #216 invariant #3) ───────────────────────
# Must happen at module import so any worker process that imports
# ``app.tasks.celery_app`` registers the postrun handler exactly once.
from . import task_dispatch as _task_dispatch  # noqa: E402

_task_dispatch.install_signal_handlers()
