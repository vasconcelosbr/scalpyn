"""Celery application configuration for Scalpyn."""

from celery import Celery
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
        "schedule": {
            "hour": 20,
            "minute": 0,
        },
    },
}
