"""
Celery Task — Profile Intelligence Engine.

Runs every 6 hours (structural queue). Iterates all users who have recent
closed shadow trades and runs the PI analysis pipeline for each.

Registered as: ``app.tasks.profile_intelligence_job.run``
"""

from __future__ import annotations

import asyncio
import logging

from .celery_app import celery_app

logger = logging.getLogger("scalpyn.tasks.profile_intelligence_job")


def _run_async(coro):
    """Run async coroutine in a sync Celery task (same pattern as autopilot)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except BaseException as exc:
            logger.debug("[_run_async] pending-task drain failed: %s", exc)

        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[_run_async] _celery_engine.dispose failed: %s", exc)

        loop.close()


async def _run_pi_job():
    """Main async logic for PI Engine job."""
    from ..database import AsyncSessionLocal
    from ..services.profile_intelligence_service import ProfileIntelligenceService
    from sqlalchemy import text

    logger.info("[PIJob] Starting Profile Intelligence job")

    svc = ProfileIntelligenceService()

    async with AsyncSessionLocal() as db:
        try:
            rows = (await db.execute(text("""
                SELECT DISTINCT user_id
                FROM shadow_trades
                WHERE created_at >= NOW() - INTERVAL '90 days'
                  AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
            """))).fetchall()
        except Exception as exc:
            logger.error("[PIJob] Failed to get user list: %s", exc)
            return

    user_ids = [row.user_id for row in rows]
    logger.info("[PIJob] Running analysis for %d users", len(user_ids))

    for user_id in user_ids:
        async with AsyncSessionLocal() as db:
            try:
                run_id = await svc.run(
                    db=db,
                    user_id=user_id,
                    lookback_days=60,
                    min_closed_trades=30,
                    include_counterfactual=True,
                    include_dynamic_combinations=True,
                    include_association_rules=False,
                    include_optuna=False,
                    include_ai_explanation=False,
                )
                logger.info("[PIJob] Completed run %s for user %s", run_id, user_id)
            except Exception as exc:
                logger.error("[PIJob] Failed for user %s: %s", user_id, exc)


@celery_app.task(name="app.tasks.profile_intelligence_job.run", bind=True)
def run(self):
    """Celery entry point for Profile Intelligence Engine."""
    _run_async(_run_pi_job())
