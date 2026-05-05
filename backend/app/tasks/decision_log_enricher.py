"""Celery task — Decision Log Enricher (Module 1)."""

import asyncio
import logging

from .celery_app import celery_app
from ..database import run_db_task, CeleryAsyncSessionLocal
from ..services.decision_log_enricher_service import DecisionLogEnricherService

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.decision_log_enricher.enrich")
def enrich() -> dict:
    """Process unprocessed ALLOW decisions and create trade_tracking rows.

    Each run fetches up to 100 decisions (oldest first), creates a
    ``trade_tracking`` row for each, and marks the decision as processed.
    The whole batch is committed atomically by ``run_db_task``.
    """

    async def _inner(session):
        service = DecisionLogEnricherService(session)
        return await service.run()

    try:
        result = asyncio.run(run_db_task(_inner, celery=True))
        logger.info("[DecisionLogEnricher] %s", result)
        return result
    except Exception as exc:
        logger.error("[DecisionLogEnricher] Task failed: %s", exc, exc_info=True)
        raise
