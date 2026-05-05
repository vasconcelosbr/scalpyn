"""Celery task — Trade Reconciliation (Module 2)."""

import asyncio
import logging

from .celery_app import celery_app
from ..database import run_db_task
from ..services.trade_reconciliation_service import TradeReconciliationService

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.trade_reconciliation.reconcile")
def reconcile() -> dict:
    """Fetch recent Gate.io fills, match against trade_tracking, and reconcile.

    Each run:
    * Fetches up to 100 spot + 100 futures fills per active exchange connection
      (last 7 days).
    * Skips any fill already registered in ``reconciled_gate_trades`` (dedup).
    * Converts matching simulated ``trade_tracking`` rows to real.
    * Creates new ``trade_tracking`` rows for fills with no local match
      (trades placed outside Scalpyn).
    """

    async def _inner(session):
        service = TradeReconciliationService(session)
        return await service.run()

    try:
        result = asyncio.run(run_db_task(_inner, celery=True))
        logger.info("[TradeReconciliation] %s", result)
        return result
    except Exception as exc:
        logger.error("[TradeReconciliation] Task failed: %s", exc, exc_info=True)
        raise
