"""Celery task for append-only Crypto EV score snapshots."""

import asyncio
import logging

from .celery_app import celery_app
from ..database import run_db_task
from ..services.crypto_ev_score_service import crypto_ev_score_service

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.crypto_ev_score.compute")
def compute() -> dict:
    async def _inner(session):
        return await crypto_ev_score_service.compute_for_all_configured_users(session)

    try:
        result = asyncio.run(run_db_task(_inner, celery=True))
        logger.info("[CryptoEV] compute result: %s", result)
        return result
    except Exception as exc:
        logger.error("[CryptoEV] compute failed: %s", exc, exc_info=True)
        raise
