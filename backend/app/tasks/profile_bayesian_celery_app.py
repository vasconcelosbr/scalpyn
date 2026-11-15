"""Minimal Celery app for the isolated Bayesian scientific worker.

The general Scalpyn Celery app imports every trading and ML task. This worker
must import only the Bayesian task module so its production image can remain
independent from XGBoost, LightGBM, CatBoost, Gate.io and MLflow.
"""

from __future__ import annotations

import os

from celery import Celery
from kombu import Exchange, Queue

from ..config import settings

QUEUE_PROFILE_BAYESIAN = "profile_bayesian"
QUEUE_PROFILE_OPTIMIZATION = "profile_optimization"

ANALYZE_TASK = "app.tasks.profile_bayesian_intelligence.analyze"
OPTIMIZE_TASK = "app.tasks.profile_bayesian_intelligence.optimize"


def _optional_rate_limit(env_name: str) -> str | None:
    value = os.getenv(env_name, "").strip()
    return value or None


celery_app = Celery(
    "scalpyn_profile_bayesian",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.profile_bayesian_intelligence"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_ignore_result=True,
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
    result_backend_transport_options={"max_connections": 2},
    worker_prefetch_multiplier=1,
    task_acks_late=False,
    task_reject_on_worker_lost=False,
    worker_max_tasks_per_child=25,
    result_expires=60,
    task_queues=(
        Queue(
            QUEUE_PROFILE_BAYESIAN,
            Exchange(QUEUE_PROFILE_BAYESIAN),
            routing_key=QUEUE_PROFILE_BAYESIAN,
        ),
        Queue(
            QUEUE_PROFILE_OPTIMIZATION,
            Exchange(QUEUE_PROFILE_OPTIMIZATION),
            routing_key=QUEUE_PROFILE_OPTIMIZATION,
        ),
    ),
    task_routes={
        ANALYZE_TASK: {"queue": QUEUE_PROFILE_BAYESIAN},
        OPTIMIZE_TASK: {"queue": QUEUE_PROFILE_OPTIMIZATION},
    },
    task_annotations={
        ANALYZE_TASK: {
            "time_limit": int(
                os.getenv("PROFILE_BAYESIAN_TASK_TIME_LIMIT_SECONDS", "7200")
            ),
            "soft_time_limit": int(
                os.getenv("PROFILE_BAYESIAN_TASK_SOFT_TIME_LIMIT_SECONDS", "6900")
            ),
            "rate_limit": _optional_rate_limit(
                "PROFILE_BAYESIAN_TASK_RATE_LIMIT"
            ),
            "max_retries": 0,
            "acks_late": False,
        },
        OPTIMIZE_TASK: {
            "time_limit": int(
                os.getenv("PROFILE_OPTIMIZATION_TASK_TIME_LIMIT_SECONDS", "7200")
            ),
            "soft_time_limit": int(
                os.getenv("PROFILE_OPTIMIZATION_TASK_SOFT_TIME_LIMIT_SECONDS", "6900")
            ),
            "rate_limit": _optional_rate_limit(
                "PROFILE_OPTIMIZATION_TASK_RATE_LIMIT"
            ),
            "max_retries": 0,
            "acks_late": False,
        },
    },
    task_default_queue="__no_default__",
    task_default_exchange="__no_default__",
    task_default_routing_key="__no_default__",
    task_create_missing_queues=False,
)
