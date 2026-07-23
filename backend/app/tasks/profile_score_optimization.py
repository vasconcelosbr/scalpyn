"""Async analysis and daily aggregation for Profile Score Intelligence."""

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


async def _analyze(run_id: str) -> dict:
    from uuid import UUID

    from ..database import get_celery_session
    from ..services.profile_score_optimization_service import (
        profile_score_optimization_service,
    )

    async with get_celery_session() as db:
        return await profile_score_optimization_service.process_global_analysis(
            db, UUID(run_id)
        )


def _run(coro) -> dict:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
        except BaseException as exc:
            logger.debug("[PI-Score] engine dispose: %s", exc)
        loop.close()


@celery_app.task(
    name="app.tasks.profile_score_optimization.analyze",
    max_retries=0,
    acks_late=False,
)
def analyze(run_id: str):
    result = _run(_analyze(run_id))
    logger.info(
        "[PI-Score] global analysis completed run=%s status=%s",
        run_id,
        result.get("status"),
    )
    return result


@celery_app.task(name="app.tasks.profile_score_optimization.refresh", max_retries=0)
def refresh():
    result = _run(_refresh())
    logger.info("[PI-Score] daily performance refreshed: %s", result)
    return result
