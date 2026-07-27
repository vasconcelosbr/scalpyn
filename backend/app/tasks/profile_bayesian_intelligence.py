"""Dedicated Profile Bayesian Intelligence Celery tasks."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from .celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except BaseException:
            pass
        try:
            from ..database import _celery_engine

            loop.run_until_complete(_celery_engine.dispose())
        except BaseException:
            pass
        loop.close()
        asyncio.set_event_loop(None)


async def _analyze(run_id: str):
    from ..database import get_celery_session
    from ..profile_bayesian.analysis_service import execute_analysis

    async with get_celery_session() as db:
        return await execute_analysis(db, UUID(run_id))


@celery_app.task(
    bind=True,
    name="app.tasks.profile_bayesian_intelligence.analyze",
    autoretry_for=(),
)
def analyze(self, run_id: str) -> dict:
    try:
        return _run_async(_analyze(run_id))
    except Exception:
        logger.exception(
            "profile_bayesian_task_failed analysis_run_id=%s task_id=%s",
            run_id,
            self.request.id,
        )
        return {"run_id": run_id, "status": "FAILED"}


async def _fail_optimization_closed(study_id: str):
    from sqlalchemy import text
    from ..database import get_celery_session

    async with get_celery_session() as db:
        await db.execute(
            text(
                """
                UPDATE profile_optimization_studies
                SET status = 'FAILED',
                    error_message = 'existing_profile_replay_engine_is_stub',
                    finished_at = now(),
                    updated_at = now()
                WHERE id = :id AND status IN ('PENDING', 'RUNNING')
                """
            ),
            {"id": study_id},
        )
        await db.commit()
    return {
        "study_id": study_id,
        "status": "FAILED",
        "reason": "existing_profile_replay_engine_is_stub",
    }


@celery_app.task(
    bind=True,
    name="app.tasks.profile_bayesian_intelligence.optimize",
    autoretry_for=(),
)
def optimize(self, study_id: str) -> dict:
    del self
    return _run_async(_fail_optimization_closed(study_id))
