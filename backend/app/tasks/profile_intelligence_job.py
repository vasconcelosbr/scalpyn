"""
Celery Task — Profile Intelligence Engine.

Runs every 24 hours (structural queue). Iterates users with recent closed
shadow trades or an enabled global Auto-Pilot, runs PI analysis, then executes
the idempotent Spot Auto-Pilot cycle.

Registered as: ``app.tasks.profile_intelligence_job.run``
"""

from __future__ import annotations

import asyncio
import logging
import os

from .celery_app import celery_app

logger = logging.getLogger("scalpyn.tasks.profile_intelligence_job")

_LOCK_TTL_S = int(os.environ.get("PROFILE_INTELLIGENCE_LOCK_TTL_S", "7200"))
_PI_ENABLE_OPTUNA = os.environ.get("PI_ENABLE_OPTUNA", "false").lower() == "true"
_PI_ENABLE_ASSOC_RULES = os.environ.get("PI_ENABLE_ASSOCIATION_RULES", "false").lower() == "true"


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
                FROM (
                    SELECT user_id
                    FROM shadow_trades
                    WHERE created_at >= NOW() - INTERVAL '90 days'
                      AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
                    UNION
                    SELECT user_id
                    FROM profile_intelligence_autopilot_settings
                    WHERE enabled IS TRUE
                ) users_to_process
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
                    include_association_rules=_PI_ENABLE_ASSOC_RULES,
                    include_optuna=_PI_ENABLE_OPTUNA,
                    include_ai_explanation=False,
                )
                logger.info("[PIJob] Completed run %s for user %s", run_id, user_id)
                from ..services.profile_intelligence_autopilot_service import (
                    ProfileIntelligenceAutopilotService,
                )
                autopilot_result = await ProfileIntelligenceAutopilotService().run_cycle(
                    db=db,
                    user_id=user_id,
                    analysis_run_id=run_id,
                )
                logger.info("[PIJob] Auto-Pilot result for user %s: %s", user_id, autopilot_result)
            except Exception as exc:
                logger.error("[PIJob] Failed for user %s: %s", user_id, exc)
            finally:
                _release_pi_lock(redis_client, lock_key)


@celery_app.task(name="app.tasks.profile_intelligence_job.run", bind=True)
def run(self):
    """Celery entry point for Profile Intelligence Engine."""
    _run_async(_run_pi_job())


async def _run_for_user(user_id, force_autopilot: bool = False):
    from uuid import UUID
    from ..database import AsyncSessionLocal
    from ..services.profile_intelligence_service import ProfileIntelligenceService
    from ..services.profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService

    uid = UUID(str(user_id))
    redis_client, acquired, lock_key = _acquire_pi_lock(uid)
    if not acquired:
        return {"status": "duplicate"}
    try:
        async with AsyncSessionLocal() as db:
            run_id = await ProfileIntelligenceService().run(
                db=db,
                user_id=uid,
                lookback_days=60,
                min_closed_trades=30,
                include_counterfactual=True,
                include_dynamic_combinations=True,
                include_association_rules=_PI_ENABLE_ASSOC_RULES,
                include_optuna=_PI_ENABLE_OPTUNA,
                include_ai_explanation=False,
            )
            return await ProfileIntelligenceAutopilotService().run_cycle(
                db=db,
                user_id=uid,
                analysis_run_id=run_id,
                force=force_autopilot,
            )
    finally:
        _release_pi_lock(redis_client, lock_key)


@celery_app.task(name="app.tasks.profile_intelligence_job.run_for_user", bind=True)
def run_for_user(self, user_id: str, force_autopilot: bool = False):
    return _run_async(_run_for_user(user_id, force_autopilot))


async def _run_cycle_only_for_user(user_id):
    """Apenas o ciclo Auto-Pilot — sem análise PI completa."""
    from uuid import UUID
    from ..database import AsyncSessionLocal
    from ..services.profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService

    uid = UUID(str(user_id))
    redis_client, acquired, lock_key = _acquire_pi_lock(f"cycle_only:{uid}")
    if not acquired:
        return {"status": "duplicate"}
    try:
        async with AsyncSessionLocal() as db:
            return await ProfileIntelligenceAutopilotService().run_cycle(
                db=db,
                user_id=uid,
                analysis_run_id=None,
                force=True,
            )
    finally:
        _release_pi_lock(redis_client, lock_key)


@celery_app.task(name="app.tasks.profile_intelligence_job.run_cycle_for_user", bind=True)
def run_cycle_for_user(self, user_id: str):
    """Celery entry point: ciclo Auto-Pilot sem PI analysis (acionado pelo botão manual)."""
    return _run_async(_run_cycle_only_for_user(user_id))


async def _monitor_autopilot():
    from ..database import AsyncSessionLocal
    from ..services.profile_intelligence_autopilot_service import ProfileIntelligenceAutopilotService
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text("""
            SELECT user_id
            FROM profile_intelligence_autopilot_settings
            WHERE enabled IS TRUE
        """))).fetchall()
    service = ProfileIntelligenceAutopilotService()
    for row in rows:
        async with AsyncSessionLocal() as db:
            try:
                await service.monitor_operational_state(db, row.user_id)
            except Exception as exc:
                logger.error("[PIJob] operational monitor failed for user %s: %s", row.user_id, exc)


@celery_app.task(name="app.tasks.profile_intelligence_job.monitor", bind=True)
def monitor(self):
    return _run_async(_monitor_autopilot())
