"""Celery tasks — Pump Monitor 30 s observation cycle and retention purge.

Runs on its own queue (``pump_monitor``) so it never competes with the 5 m
indicator chain or the long ``pump_radar`` backfills.
"""
import logging

from ..tasks.celery_app import celery_app
from .radar_auto_discover import _run_async

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.pump_monitor.cycle")
def cycle():
    from ..services.pump_monitor_service import run_cycle
    return _run_async(run_cycle())


@celery_app.task(name="app.tasks.pump_monitor.purge")
def purge():
    from ..database import run_db_task
    from ..services.pump_monitor_service import purge as _purge
    return _run_async(run_db_task(_purge, celery=True))
