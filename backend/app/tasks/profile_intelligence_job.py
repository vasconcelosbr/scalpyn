"""
Celery Task — Profile Intelligence Engine.

Runs every 6 hours (structural queue). Iterates all users who have recent
closed shadow trades and runs the PI analysis pipeline for each.

Registered as: ``app.tasks.profile_intelligence_job.run``
"""

from __future__ import annotations

import asyncio
import logging
import os

from .celery_app import celery_app

logger = logging.getLogger("scalpyn.tasks.profile_intelligence_job")

_LOCK_TTL_S = int(os.environ.get("PROFILE_INTELLIGENCE_LOCK_TTL_S", "7200"))


def _acquire_pi_lock(user_id) -> tuple:
    """Try to acquire a per-user Redis lock. Returns (client, acquired)."""
    try:
        import redis as _redis_lib
        from ..config import settings
        r = _redis_lib.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        key = f"pi_engine:run_lock:{user_id}"
        acquired = bool(r.set(key, "1", nx=True, ex=_LOCK_TTL_S))
        return r, acquired, key
    except Exception as exc:
        logger.warning("[PIJob] Redis lock unavailable (%s) — proceeding without lock", exc)
        return None, True, None  # fail open


def _release_pi_lock(client, key):
    if client and key:
        try:
            client.delete(key)
        except Exception:
            pass


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
        redis_client, acquired, lock_key = _acquire_pi_lock(user_id)
        if not acquired:
            logger.info("[PIJob] Lock exists for user %s — skipping (another run in progress)", user_id)
            continue

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
            finally:
                _release_pi_lock(redis_client, lock_key)


@celery_app.task(name="app.tasks.profile_intelligence_job.run", bind=True)
def run(self):
    """Celery entry point for Profile Intelligence Engine."""
    _run_async(_run_pi_job())
