"""Celery Task — Auto-discover assets for pools with auto_refresh enabled."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _discover_async():
    from ..database import AsyncSessionLocal
    from ..models.pool import Pool, PoolCoin
    from ..exchange_adapters.gate_adapter import GateAdapter
    from sqlalchemy import select

    logger.info("Auto-discover assets: starting run...")
    total_added = 0
    total_removed = 0
    pools_processed = 0

    async with AsyncSessionLocal() as db:
        # Find all active pools with auto_refresh=true in overrides
        result = await db.execute(
            select(Pool).where(
                Pool.is_active == True,
                text("(overrides->>'auto_refresh')::boolean = true"),
            )
        )
        pools = result.scalars().all()

        adapter = GateAdapter(api_key="", api_secret="")

        for pool in pools:
            try:
                market_type = pool.market_type or "spot"
                overrides = pool.overrides or {}
                auto_add = overrides.get("auto_add", True)
                auto_remove = overrides.get("auto_remove", False)
                min_volume = float(overrides.get("min_volume_24h", 0))

                # Fetch universe
                if market_type == "futures":
                    raw_pairs = await adapter.list_futures_contracts()
                    universe_symbols: set[str] = {p["name"] for p in raw_pairs}
                else:
                    raw_pairs = await adapter.list_spot_pairs()
                    universe_symbols = {
                        p["id"]
                        for p in raw_pairs
                        if p.get("quote", "") == "USDT"
                        and p.get("trade_status") == "tradable"
                    }

                # Volume filter
                if min_volume > 0:
                    try:
                        tickers = await adapter.get_tickers(symbols=None, market=market_type)
                        if market_type == "futures":
                            vol_map = {
                                t.get("contract", ""): float(t.get("volume_24h_quote", 0) or 0)
                                for t in tickers
                            }
                        else:
                            vol_map = {
                                t.get("currency_pair", ""): float(t.get("quote_volume", 0) or 0)
                                for t in tickers
                            }
                        universe_symbols = {
                            s for s in universe_symbols if vol_map.get(s, 0) >= min_volume
                        }
                    except Exception as e:
                        logger.warning(f"Ticker fetch failed for pool {pool.name}: {e}")

                # Load existing coins
                coins_result = await db.execute(
                    select(PoolCoin).where(PoolCoin.pool_id == pool.id)
                )
                existing_coins = coins_result.scalars().all()
                existing_manual = {
                    c.symbol for c in existing_coins if (c.origin or "manual") == "manual"
                }
                existing_discovered = {
                    c.symbol: c
                    for c in existing_coins
                    if (c.origin or "manual") == "discovered"
                }

                now = datetime.now(timezone.utc)
                added = 0
                removed = 0

                if auto_add:
                    to_add = universe_symbols - existing_manual - set(existing_discovered.keys())
                    for symbol in to_add:
                        db.add(PoolCoin(
                            pool_id=pool.id,
                            symbol=symbol,
                            market_type=market_type,
                            is_active=True,
                            origin="discovered",
                            discovered_at=now,
                        ))
                        added += 1

                if auto_remove:
                    to_remove = set(existing_discovered.keys()) - universe_symbols
                    for symbol in to_remove:
                        await db.delete(existing_discovered[symbol])
                        removed += 1

                await db.commit()

                logger.info(
                    f"Pool '{pool.name}': +{added} -{removed} assets "
                    f"(universe={len(universe_symbols)})"
                )
                total_added += added
                total_removed += removed
                pools_processed += 1

                # WebSocket notify if enabled
                if overrides.get("notify_on_changes") and (added > 0 or removed > 0):
                    try:
                        from ..websocket.scalpyn_ws_server import broadcast_alert
                        await broadcast_alert(
                            str(pool.user_id),
                            "DISCOVER_COMPLETE",
                            {
                                "pool_id": str(pool.id),
                                "pool_name": pool.name,
                                "added": added,
                                "removed": removed,
                            },
                        )
                    except Exception:
                        pass  # WS not critical path

            except Exception as e:
                logger.error(f"Auto-discover failed for pool '{pool.name}': {e}")
                await db.rollback()
                continue

    logger.info(
        f"Auto-discover complete: {pools_processed} pools, "
        f"+{total_added} -{total_removed} assets total"
    )
    return f"{pools_processed} pools | +{total_added} -{total_removed}"


@celery_app.task(name="app.tasks.auto_discover_assets.discover")
def discover():
    return _run_async(_discover_async())
