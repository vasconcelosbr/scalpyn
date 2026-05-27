"""
Celery Task — Auto-Pilot Engine.

Runs every 6 hours (structural queue).

Flow:
  1. Load all profiles with auto_pilot_enabled=True
  2. For each profile, run autopilot_engine.run_autopilot_cycle()
  3. If MUTATED, apply new config + updated auto_pilot_config to profile
  4. Log results

Registered as: ``app.tasks.autopilot.run``
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .celery_app import celery_app

logger = logging.getLogger("scalpyn.tasks.autopilot")


def _run_async(coro):
    """Run async coroutine in a sync Celery task (standard asyncpg-safe pattern)."""
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

        try:
            from ..database import _celery_engine as _ce
            sync_pool = _ce.sync_engine.pool
            records = list(getattr(sync_pool, "_all_conns", None) or [])
            for record in records:
                raw = (
                    getattr(record, "dbapi_connection", None)
                    or getattr(record, "connection", None)
                )
                asyncpg_conn = (
                    getattr(raw, "_connection", None)
                    or getattr(raw, "connection", None)
                    or raw
                )
                terminate = getattr(asyncpg_conn, "terminate", None)
                if callable(terminate):
                    try:
                        terminate()
                    except BaseException:
                        pass
        except BaseException as exc:
            logger.debug("[_run_async] hard-terminate sweep failed: %s", exc)

        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[_run_async] shutdown_asyncgens failed: %s", exc)

        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[_run_async] loop.close failed: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


async def _run_autopilot() -> dict[str, Any]:
    from datetime import datetime, timezone
    from sqlalchemy import select

    from ..database import CeleryAsyncSessionLocal
    from ..models.profile import Profile
    from ..services.autopilot_engine import run_autopilot_cycle

    results: list[dict] = []

    async with CeleryAsyncSessionLocal() as db:
        # Load all autopilot-enabled profiles
        query = select(Profile).where(Profile.auto_pilot_enabled.is_(True))
        result = await db.execute(query)
        profiles = result.scalars().all()

    logger.info(f"[Autopilot] Starting cycle — {len(profiles)} profile(s) enabled")

    for profile in profiles:
        profile_id = str(profile.id)
        user_id = str(profile.user_id)
        profile_role = getattr(profile, "profile_role", None) or "primary_filter"
        current_config = dict(profile.config or {})
        ap_config = dict(getattr(profile, "auto_pilot_config", None) or {})

        if not profile.profile_role:
            logger.warning(f"[Autopilot] Profile {profile_id} sem role — pulando")
            results.append({"profile_id": profile_id, "action": "SKIPPED", "reason": "no_role"})
            continue

        logger.info(f"[Autopilot] Running cycle for profile {profile_id} ({profile_role})")

        try:
            async with CeleryAsyncSessionLocal() as cycle_db:
                cycle_result = await run_autopilot_cycle(
                    profile_id=profile_id,
                    profile_role=profile_role,
                    user_id=user_id,
                    current_config=current_config,
                    auto_pilot_config=ap_config,
                    db=cycle_db,
                )

            # If mutation happened, apply new config to profile
            if cycle_result.get("action") == "MUTATED":
                async with CeleryAsyncSessionLocal() as apply_db:
                    apply_result = await apply_db.execute(
                        select(Profile).where(Profile.id == profile.id)
                    )
                    p = apply_result.scalar_one_or_none()
                    if p:
                        p.config = cycle_result["new_config"]
                        p.auto_pilot_config = cycle_result["updated_ap_config"]
                        p.updated_at = datetime.now(timezone.utc)
                        await apply_db.commit()
                        logger.info(
                            f"[Autopilot] Mutation applied to profile {profile_id}: "
                            f"{cycle_result['reason']}"
                        )

            results.append({"profile_id": profile_id, **cycle_result})

        except Exception as e:
            logger.error(f"[Autopilot] Error in cycle for profile {profile_id}: {e}", exc_info=True)
            results.append({"profile_id": profile_id, "action": "ERROR", "reason": str(e)})

    mutated = sum(1 for r in results if r.get("action") == "MUTATED")
    analyzed = sum(1 for r in results if r.get("action") == "ANALYZED")
    errors = sum(1 for r in results if r.get("action") == "ERROR")

    logger.info(
        f"[Autopilot] Cycle complete — "
        f"mutated={mutated} analyzed={analyzed} errors={errors} total={len(results)}"
    )

    return {
        "profiles_processed": len(results),
        "mutated": mutated,
        "analyzed": analyzed,
        "errors": errors,
        "results": results,
    }


@celery_app.task(name="app.tasks.autopilot.run")
def run():
    """Auto-Pilot Engine — runs for all autopilot-enabled profiles."""
    return _run_async(_run_autopilot())
