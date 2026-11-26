"""Dedicated observation queue; never publishes execution or Shadow work."""
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select, text
from .celery_app import celery_app


@celery_app.task(name='app.tasks.mtf_history.dispatch', acks_late=False, soft_time_limit=240, time_limit=270)
def dispatch():
    from .compute_indicators import _run_async
    return _run_async(dispatch_async())


async def dispatch_async():
    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..services import mtf_history as history
    from ..services.mtf_chart_service import load_config
    count = 0
    now = int(datetime.now(timezone.utc).timestamp())
    async with CeleryAsyncSessionLocal() as db:
        users = (await db.execute(select(ConfigProfile.user_id).where(
            ConfigProfile.config_type=='mtf_analysis', ConfigProfile.pool_id.is_(None),
            ConfigProfile.is_active.is_(True)))).scalars().all()
        for uid in set(users):
            cfg = await load_config(db,uid)
            hc = await history.config_service.get_config(db,'mtf_history',uid)
            if not cfg.enabled:
                continue
            from ..services.mtf_chart_engine import TF_SECONDS
            interval = min(TF_SECONDS[tf] for tf in cfg.active_timeframes())
            scheduled = datetime.fromtimestamp(now-now%interval,timezone.utc).isoformat()
            for wid in cfg.monitored_watchlists:
                for item in await history.history_members(db,uid,wid):
                    import json
                    # Full universe. This does not call persist_evaluation or any Shadow task.
                    item = json.loads(history.serial(item))
                    key = history.canonical_hash(dict(user=str(uid),watchlist=wid,symbol=item['symbol'],profile=str(item.get('profile_id')),scheduled_at=scheduled))
                    await db.execute(text('''INSERT INTO mtf_history_capture_jobs(identity,user_id,watchlist_id,member,scheduled_at)
                        VALUES(:key,:uid,:wid,CAST(:member AS jsonb),:scheduled) ON CONFLICT DO NOTHING'''),
                        dict(key=key,uid=uid,wid=wid,member=history.serial(item),scheduled=history.utc(scheduled)))
                    count += 1
        await db.commit()
        await dispatch_pending(db)
    return {'scheduled':count,'operational_effect':False}


@celery_app.task(name='app.tasks.mtf_history.capture', acks_late=False,
                 autoretry_for=(Exception,), retry_backoff=True, max_retries=2,
                 soft_time_limit=180, time_limit=210)
def capture(key):
    from .compute_indicators import _run_async
    async def run():
        from ..database import CeleryAsyncSessionLocal
        from ..services.mtf_history import capture as save
        async with CeleryAsyncSessionLocal() as db:
            row = (await db.execute(text("SELECT * FROM mtf_history_capture_jobs WHERE identity=:key AND status='PENDING' FOR UPDATE SKIP LOCKED"),{'key':key})).mappings().first()
            if not row:
                return False
            try:
                result = await save(db,row['user_id'],row['watchlist_id'],row['member'],row['scheduled_at'].isoformat())
                await db.execute(text("UPDATE mtf_history_capture_jobs SET status='COMPLETE',error=NULL WHERE identity=:key"),{'key':key})
                await db.commit()
                return result
            except Exception as exc:
                await db.rollback()
                await db.execute(text("UPDATE mtf_history_capture_jobs SET attempts=attempts+1,error=:error WHERE identity=:key"),{'key':key,'error':str(exc)[:500]})
                await db.commit()
                raise
    return _run_async(run())


async def dispatch_pending(db):
    rows = (await db.execute(text("""SELECT identity FROM mtf_history_capture_jobs WHERE status='PENDING'
        AND (dispatched_at IS NULL OR dispatched_at<now()-interval '10 minutes')
        ORDER BY scheduled_at FOR UPDATE SKIP LOCKED"""))).scalars().all()
    for key in rows:
        await db.execute(text('UPDATE mtf_history_capture_jobs SET dispatched_at=now() WHERE identity=:key'),{'key':key})
    # Release the claimed rows before a fast consumer can attempt SKIP LOCKED.
    # A crash or failed publish is recovered by the durable dispatch lease.
    await db.commit()
    for key in rows:
        from .task_dispatch import enqueue
        enqueue('app.tasks.mtf_history.capture',dedup_key='mtf-history:'+key,ttl_seconds=600,args=(key,))


@celery_app.task(name='app.tasks.mtf_history.maintenance', acks_late=False, soft_time_limit=240, time_limit=270)
def maintenance():
    from .compute_indicators import _run_async
    async def run():
        from ..database import CeleryAsyncSessionLocal
        from ..services.mtf_history import advance_outcomes
        from ..services.mtf_history_export import process_pending
        async with CeleryAsyncSessionLocal() as db:
            await dispatch_pending(db)
            results = await advance_outcomes(db)
            exports = await process_pending(db)
            return {'outcomes':results,'exports':exports,'operational_effect':False}
    return _run_async(run())
