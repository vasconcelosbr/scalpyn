"""Celery task — Trade Monitor (Module 3).

Scans open ``trade_tracking`` rows every 10 seconds and closes any that
have hit their TP price, SL price, or holding-time timeout.

Registered as: ``app.tasks.trade_monitor.monitor``
Queue: execution (latency-sensitive, must run on isolated workers).
"""

import asyncio
import logging

from .celery_app import celery_app
from ..config import settings
from ..database import run_db_task
from ..services.trade_monitor_service import TradeMonitorService

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.trade_monitor.monitor", bind=True, max_retries=0)
def monitor(self) -> str:
    """Celery periodic task — trade monitor.

    Scheduled every 10 seconds via beat_schedule in celery_app.py.
    Returns a human-readable summary string for the Celery result backend.
    """
    async def _inner(session):
        service = TradeMonitorService(session)
        return await service.run(timeout_seconds=settings.TRADE_MONITOR_TIMEOUT_SECONDS)

    try:
        result = asyncio.run(run_db_task(_inner, celery=True))
        logger.info("[TradeMonitor] task complete: %s", result)
        closed = (
            result.get("closed_tp", 0)
            + result.get("closed_sl", 0)
            + result.get("closed_timeout", 0)
        )
        return (
            f"TradeMonitor: {result.get('open_trades', 0)} open, "
            f"{closed} closed, {result.get('errors', 0)} errors"
        )
    except Exception as exc:
        logger.error("[TradeMonitor] task failed: %s", exc, exc_info=True)
        raise
