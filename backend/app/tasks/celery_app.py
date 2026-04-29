"""Celery application configuration for Scalpyn."""

from celery import Celery
from celery.schedules import crontab
from ..config import settings

celery_app = Celery(
    "scalpyn_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.collect_market_data",
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
    ],
)

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
}
