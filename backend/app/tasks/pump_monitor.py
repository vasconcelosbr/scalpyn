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


@celery_app.task(name="app.tasks.pump_monitor.refresh_listing_contracts")
def refresh_listing_contracts():
    import asyncio
    from ..services.pump_opportunity_service import refresh_listing_contracts as refresh
    return _run_async(asyncio.wait_for(refresh(),20))


@celery_app.task(name="app.tasks.pump_monitor.train_ml_daily")
def train_ml_daily(owner=None, force=False, family="observation"):
    """Daily Pump directional challenger (2026-10-07), formerly the Railway
    ``scalpyn-pump-ml`` upload-source cron. Same ledger, lock and one-run-per-UTC-day
    rule: if the Railway job runs first it owns the day and this one no-ops.
    ``owner``+``force`` come only from the authenticated manual trigger."""
    from ..services.pump_ml_daily import run_daily
    result = _run_async(run_daily(owner=owner, force=bool(force), family=str(family or "observation")))
    logger.info("[PUMP-ML] daily run result=%s", {k: (v or {}).get("status") for k, v in (result or {}).items()})
    return result
