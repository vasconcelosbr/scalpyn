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
        "app.tasks.evaluate_signals"
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# Setup Periodic Tasks
celery_app.conf.beat_schedule = {
    "collect_market_data_every_minute": {
        "task": "app.tasks.collect_market_data.collect_all",
        "schedule": 60.0,
    },
}
