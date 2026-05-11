"""Celery Task — daily summary notification."""

import asyncio
import logging

from sqlalchemy import select

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run async code in sync Celery task.

    Drains pending asyncpg callbacks (NullPool connection close, etc.)
    before closing the loop. Without this, callbacks scheduled by asyncpg
    during connection cleanup hit a closed loop, leaving sessions in
    PendingRollbackError and poisoning the next task invocation.
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
