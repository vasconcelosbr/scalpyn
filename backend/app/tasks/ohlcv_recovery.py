"""Explicitly dispatched historical recovery; never runs on the canonical queue."""
import asyncio
import json
from uuid import UUID
from sqlalchemy import text
from .celery_app import celery_app
from .ohlcv_backfill import _run_async


async def recover_async(job_id):
    from ..database import CeleryAsyncSessionLocal
    from ..services.ohlcv_recovery import run_page, load_config
    async with CeleryAsyncSessionLocal() as db:
        owner = (await db.execute(text(
            'SELECT submitted_by FROM ohlcv_recovery_jobs WHERE id=:id'
        ), {'id': job_id})).scalar_one()
        cfg = await load_config(db, owner)
    result = {'status': 'DISABLED'}
    if not cfg.enabled:
        return result
    for page in range(cfg.max_pages_per_run):
        async with CeleryAsyncSessionLocal() as db:
            result = await run_page(db, job_id)
        if result['status'] != 'PARTIAL' or result.get('error_code') or result.get('remaining_page_gaps'):
            break
        if page + 1 < cfg.max_pages_per_run:
            await asyncio.sleep(cfg.request_spacing_seconds)
    return result


@celery_app.task(name='app.tasks.ohlcv_recovery.recover', max_retries=0)
def recover(job_id):
    return json.dumps(_run_async(recover_async(UUID(job_id))), default=str)
