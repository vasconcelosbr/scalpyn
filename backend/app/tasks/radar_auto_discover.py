"""Celery Task — sync pool assets from the Market Catalyst Radar feed for pools with radar_enabled."""

import asyncio
import logging
from datetime import datetime, timezone


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
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # Step 1 — cancel and drain pending asyncio tasks.
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except BaseException as exc:
            logger.debug("[_run_async] pending-task drain failed: %s", exc)

        # Step 2 — graceful engine dispose (closes asyncpg sockets in-loop).
        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[_run_async] _celery_engine.dispose failed: %s", exc)

        # Step 3 — hard-terminate any asyncpg connection still cached on the pool.
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

        # Step 4 — drain async generators registered on the loop.
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[_run_async] shutdown_asyncgens failed: %s", exc)

        # Step 5 — close the loop. Always last; never propagate.
        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[_run_async] loop.close failed: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


async def _radar_sync_async():
    from ..database import run_db_task
    from ..models.pool import Pool
    from ..services.ai_keys_service import get_decrypted_api_key
    from ..services.radar_service import fetch_top_assets, RadarFeedUnavailable
    from ..services.radar_pool_sync import reconcile_radar_pool
    from ..services.radar_feed_audit import record_receipt, complete_receipt
    from ..utils.symbol_filters import is_excluded_asset
    from sqlalchemy import select

    async def _load_pools(db):
        result = await db.execute(select(Pool).where(
            Pool.is_active.is_(True), Pool.market_type == "spot",
            Pool.overrides["radar_enabled"].as_boolean().is_(True),
        ))
        return [{"id": p.id, "name": p.name, "user_id": p.user_id}
                for p in result.scalars().all()]

    pools = await run_db_task(_load_pools, celery=True)
    total_added = total_removed = processed = 0
    feeds = {}
    receipts = {}
    for pool in pools:
        user_id = pool["user_id"]
        if user_id not in feeds:
            assets = None
            try:
                async def _load_key(db, uid=user_id):
                    return await get_decrypted_api_key(db, uid, "radar")
                key = await run_db_task(_load_key, celery=True)
                if not key:
                    feeds[user_id] = (None, "no_radar_key")
                else:
                    assets = await fetch_top_assets(key)
                    feeds[user_id] = ({a["pair"] for a in assets
                        if a["pair"].endswith("_USDT") and not is_excluded_asset(a["pair"])}, None)
            except RadarFeedUnavailable as exc:
                feeds[user_id] = (None, str(exc))
            except Exception as exc:
                # Log only the type: provider errors may include request details.
                logger.warning("Radar fetch unavailable: %s", type(exc).__name__)
                feeds[user_id] = (None, "fetch_failed")
            receipts[user_id] = (assets, datetime.now(timezone.utc))
        pairs, reason = feeds[user_id]
        receipt_id = None
        if reason is None:
            receipt_assets, received_at = receipts[user_id]
        else:
            receipt_assets, received_at = None, receipts[user_id][1]
        try:
            async def _audit(db):
                return await record_receipt(db, pool_id=pool["id"],
                    assets=receipt_assets, received_at=received_at,
                    selected_pairs=pairs or set(), reason=reason)
            receipt_id = await run_db_task(_audit, celery=True)
        except Exception:
            logger.exception("Radar audit receipt failed for pool=%s", pool["id"])
        reconciled = False
        skipped = False
        try:
            async def _persist(db, p=pool, selection=pairs, failure=reason):
                return await reconcile_radar_pool(db, pool_id=p["id"],
                    user_id=p["user_id"], radar_pairs=selection, reason=failure)
            stats = await run_db_task(_persist, celery=True)
            reconciled = True
            skipped = stats.get("skipped", False)
            total_added += stats["added"]
            total_removed += stats["removed"]
            processed += 1
            logger.info("Radar pool=%s health=%s result=%s", pool["name"],
                        reason or "healthy", stats)
        except Exception:
            logger.exception("Radar reconciliation failed for pool=%s", pool["id"])
        if receipt_id is not None and reason is None:
            try:
                async def _complete(db):
                    await complete_receipt(db, receipt_id, reconciled=reconciled, skipped=skipped)
                await run_db_task(_complete, celery=True)
            except Exception:
                logger.exception("Radar audit reconciliation observation failed for pool=%s", pool["id"])
    return f"{processed} pools | +{total_added} -{total_removed}"


@celery_app.task(name="app.tasks.radar_auto_discover.purge_audit")
def purge_audit():
    from ..database import run_db_task
    from ..services.radar_feed_audit import purge_expired
    return _run_async(run_db_task(purge_expired, celery=True))


@celery_app.task(name="app.tasks.radar_auto_discover.sync")
def sync():
    return _run_async(_radar_sync_async())
