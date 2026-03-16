"""Celery Task — daily summary notification."""

import asyncio
import logging

from sqlalchemy import select

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _daily_summary_async():
    from ..database import AsyncSessionLocal
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
