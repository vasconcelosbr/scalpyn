"""Celery Task — sync pool assets from the Market Catalyst Radar feed for pools with radar_enabled."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

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
    from ..models.pool import Pool, PoolAssetExclusion, PoolCoin
    from ..services.ai_keys_service import get_decrypted_api_key
    from ..services.radar_service import fetch_top_assets
    from ..utils.symbol_filters import is_excluded_asset
    from sqlalchemy import select

    logger.info("Radar sync: starting run...")

    # Radar (Gate.io spot, buy-pressure heuristic) only covers spot; a
    # futures pool has no equivalent feed, so it is excluded up front rather
    # than silently no-op'd per pool.
    async def _load_pools(db):
        result = await db.execute(
            select(Pool).where(
                Pool.is_active == True,
                Pool.market_type == "spot",
                text("(overrides->>'radar_enabled')::boolean = true"),
            )
        )
        pools = result.scalars().all()
        return [{"id": p.id, "name": p.name, "user_id": p.user_id} for p in pools]

    pool_data_list = await run_db_task(_load_pools, celery=True)
    if not pool_data_list:
        logger.info("Radar sync: no pool with radar_enabled=true; no mutation performed.")
        return "0 pools | radar disabled"

    total_added = 0
    total_removed = 0
    pools_processed = 0
    # One tenant may own several radar-enabled pools; the feed is identical
    # for all of them, so fetch it at most once per distinct user per run.
    assets_by_user: dict = {}

    for pd in pool_data_list:
        try:
            user_id = pd["user_id"]
            if user_id not in assets_by_user:
                async def _load_key(db, _uid=user_id):
                    return await get_decrypted_api_key(db, _uid, "radar")

                api_key = await run_db_task(_load_key, celery=True)
                if not api_key:
                    logger.warning("[RADAR-SKIP] pool=%s reason=no_radar_key", pd["name"])
                    assets_by_user[user_id] = set()
                else:
                    assets = await fetch_top_assets(api_key)
                    assets_by_user[user_id] = {
                        a["pair"] for a in assets
                        if a.get("pair", "").endswith("_USDT") and not is_excluded_asset(a["pair"])
                    }

            radar_pairs = assets_by_user[user_id]
            if not radar_pairs:
                pools_processed += 1
                continue

            async def _persist(db, _pd=pd, _radar_pairs=radar_pairs):
                coins_result = await db.execute(
                    select(PoolCoin).where(PoolCoin.pool_id == _pd["id"])
                )
                existing_coins = coins_result.scalars().all()
                exclusions_result = await db.execute(
                    select(PoolAssetExclusion.symbol).where(
                        PoolAssetExclusion.pool_id == _pd["id"]
                    )
                )
                excluded_symbols = set(exclusions_result.scalars().all())
                existing_radar = {
                    c.symbol: c for c in existing_coins if (c.origin or "manual") == "radar"
                }
                other_origin = {
                    c.symbol for c in existing_coins if (c.origin or "manual") != "radar"
                }

                to_add = _radar_pairs - excluded_symbols - other_origin - set(existing_radar.keys())
                to_remove = set(existing_radar.keys()) - _radar_pairs

                now = datetime.now(timezone.utc)
                for symbol in to_add:
                    db.add(PoolCoin(
                        pool_id=_pd["id"], symbol=symbol, market_type="spot",
                        is_active=True, origin="radar", discovered_at=now,
                    ))
                for symbol in to_remove:
                    await db.delete(existing_radar[symbol])
                # run_db_task auto-commits on successful exit
                return len(to_add), len(to_remove)

            added, removed = await run_db_task(_persist, celery=True)
            logger.info(
                "Pool '%s': radar +%d -%d (top_assets=%d)",
                pd["name"], added, removed, len(radar_pairs),
            )
            total_added += added
            total_removed += removed
            pools_processed += 1

        except Exception as e:
            logger.error("Radar sync failed for pool '%s': %s", pd["name"], e)
            continue

    logger.info(
        "Radar sync complete: %d pools, +%d -%d assets total",
        pools_processed, total_added, total_removed,
    )
    return f"{pools_processed} pools | +{total_added} -{total_removed}"


@celery_app.task(name="app.tasks.radar_auto_discover.sync")
def sync():
    return _run_async(_radar_sync_async())
