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
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# Periodic task schedule
celery_app.conf.beat_schedule = {
    # Full pipeline every 60 seconds
    "collect_market_data_every_minute": {
        "task": "app.tasks.collect_market_data.collect_all",
        "schedule": 60.0,
    },
    # Daily summary at 20:00 UTC
    "daily_summary": {
        "task": "app.tasks.daily_summary.send",
        "schedule": crontab(hour=20, minute=0),
    },
    # Anti-liquidation monitor every 30 seconds
    "anti_liq_monitor": {
        "task": "app.tasks.anti_liq_monitor.monitor",
        "schedule": 30.0,
    },
    # Macro regime update every 30 minutes
    "macro_regime_update": {
        "task": "app.tasks.macro_regime_update.update",
        "schedule": 1800.0,
    },
    # Auto-discover assets for pools with auto_refresh=true every 1 hour
    "auto_discover_assets_hourly": {
        "task": "app.tasks.auto_discover_assets.discover",
        "schedule": 3600.0,
    },
    # Buy execution cycle every 60 seconds (SpotEngineConfig-driven)
    "execute_buy_cycle": {
        "task": "app.tasks.execute_buy.execute_buy_cycle",
        "schedule": 60.0,
    },
    # Fetch market caps from CoinMarketCap every 30 minutes
    "fetch_market_caps": {
        "task": "app.tasks.fetch_market_caps.fetch_market_caps",
        "schedule": 1800.0,
    },
}
