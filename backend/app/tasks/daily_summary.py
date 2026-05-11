"""Celery Task — daily summary notification."""

import asyncio
import logging

from sqlalchemy import select

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run async coroutine in a sync Celery task.

    Creates a dedicated event loop per task invocation. Drains all pending
    asyncpg tasks and disposes the NullPool engine before closing the loop.

    Without dispose + drain, asyncpg schedules _terminate_graceful_close
    via loop.create_task() during GC of NullPool connections after loop.close(),
    causing RuntimeError: Event loop is closed on the next invocation.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except BaseException:
            pass
        finally:
            try:
                from ..database import _celery_engine
                loop.run_until_complete(_celery_engine.dispose())
            except Exception:
                pass
            loop.close()


async def _daily_summary_async():
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..services.analytics_service import analytics_service
    from ..services.notification_service import notification_service
    from ..models.user import User

    logger.info("Generating daily summaries...")
    sent = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_active == True))
        users = result.scalars().all()

        for user in users:
            try:
                summary = await analytics_service.get_daily_summary(db, user.id)
                await notification_service.send_daily_summary(db, user.id, summary)
                sent += 1
            except Exception as e:
                logger.warning(f"Failed daily summary for user {user.id}: {e}")

    logger.info(f"Daily summaries sent: {sent}")
    return sent


@celery_app.task(name="app.tasks.daily_summary.send")
def send():
    count = _run_async(_daily_summary_async())
    return f"Sent {count} daily summaries"
