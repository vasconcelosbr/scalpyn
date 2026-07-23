"""Daily aggregation for Profile Score Intelligence shadow challengers."""

from __future__ import annotations

import asyncio
import logging

from .celery_app import celery_app

logger = logging.getLogger(__name__)


async def _refresh() -> dict:
    from ..database import get_celery_session
    from ..services.profile_score_optimization_service import (
        profile_score_optimization_service,
    )

    async with get_celery_session() as db:
        result = await profile_score_optimization_service.refresh_performance(db)
        await db.commit()
        return result


@celery_app.task(name="app.tasks.profile_score_optimization.refresh", max_retries=0)
def refresh():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_refresh())
        logger.info("[PI-Score] daily performance refreshed: %s", result)
        return result
    finally:
        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
        except BaseException as exc:
            logger.debug("[PI-Score] engine dispose: %s", exc)
        loop.close()
