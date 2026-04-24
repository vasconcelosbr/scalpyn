"""Celery Task — Auto-discover assets for pools with auto_refresh enabled."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from ..tasks.celery_app import celery_app
from ..services.pool_selection import (
    apply_pool_discovery_filters,
    extract_profile_discovery_thresholds,
)

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _discover_async():
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..models.pool import Pool, PoolCoin
    from ..models.profile import Profile
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
                max_assets = int(overrides.get("max_assets", 0))
                min_volume = 0.0
                min_market_cap = 0.0

                if pool.profile_id:
                    profile = (await db.execute(
                        select(Profile).where(Profile.id == pool.profile_id)
                    )).scalars().first()
                    if profile and profile.config:
                        min_volume, min_market_cap, _ = extract_profile_discovery_thresholds(
                            profile.config,
                        )
                if min_volume <= 0:
                    min_volume = float(overrides.get("min_volume_24h", 0) or 0)

                # Fetch universe
                from ..utils.symbol_filters import is_excluded_asset

                if market_type == "futures":
                    raw_pairs = await adapter.list_futures_contracts()
                    universe_symbols: set[str] = {
                        p["name"] for p in raw_pairs
                        if not is_excluded_asset(p["name"])
                    }
                    vol_map: dict[str, float] = {}
                    logger.info(
                        "Pool '%s': futures universe %d contracts (from %d raw)",
                        pool.name, len(universe_symbols), len(raw_pairs),
                    )
                else:
                    # Primary: use tickers (public, no auth) — only active pairs
                    raw_tickers = await adapter.list_spot_tickers_public()
                    universe_symbols = set()
                    vol_map: dict[str, float] = {}
                    for t in raw_tickers:
                        pair = t.get("currency_pair", "")
                        if not pair.endswith("_USDT"):
                            continue
                        if is_excluded_asset(pair):
                            continue
                        last = float(t.get("last", 0) or 0)
                        if last <= 0:
                            continue
                        universe_symbols.add(pair)
                        vol_map[pair] = float(t.get("quote_volume", 0) or 0)

                    # Fallback: supplement with currency_pairs if too few
                    if len(universe_symbols) < 200:
                        logger.warning(
                            "Pool '%s': ticker universe small (%d), "
                            "supplementing with currency_pairs",
                            pool.name, len(universe_symbols),
                        )
                        try:
                            raw_pairs = await adapter.list_spot_pairs()
                            for p in raw_pairs:
                                sym = p.get("id", "")
                                if (
                                    p.get("quote", "") == "USDT"
                                    and p.get("trade_status") in (
                                        "tradable", "buyable", "sellable",
                                    )
                                    and not is_excluded_asset(sym)
                                ):
                                    universe_symbols.add(sym)
                        except Exception as e:
                            logger.warning(
                                "Pool '%s': currency_pairs fallback failed: %s",
                                pool.name, e,
                            )

                    logger.info(
                        "Pool '%s': spot universe %d assets (from %d raw tickers, "
                        "after leveraged/stablecoin/active filter)",
                        pool.name, len(universe_symbols), len(raw_tickers),
                    )

                # Volume filter
                if min_volume > 0:
                    if market_type == "futures":
                        # Futures need a separate ticker fetch for volume data
                        try:
                            fut_tickers = await adapter._public_get(
                                f"{adapter.FUTURES_BASE}/futures/{adapter.SETTLE}/tickers"
                            )
                            vol_map = {
                                t.get("contract", ""): float(t.get("volume_24h_quote", 0) or 0)
                                for t in fut_tickers
                            }
                        except Exception as e:
                            logger.warning(f"Futures ticker fetch failed for pool {pool.name}: {e}")
                            vol_map = {}
                    # For spot, vol_map was pre-built from tickers above

                market_cap_map = {}
                if min_market_cap > 0:
                    market_cap_rows = (await db.execute(text("""
                        SELECT symbol, market_cap
                        FROM market_metadata
                        WHERE symbol = ANY(:symbols)
                    """), {"symbols": list(universe_symbols)})).fetchall()
                    market_cap_map = {
                        row.symbol: float(row.market_cap)
                        for row in market_cap_rows
                        if row.market_cap is not None
                    }

                selection = apply_pool_discovery_filters(
                    universe_symbols,
                    vol_map=vol_map,
                    market_cap_map=market_cap_map,
                    min_volume=min_volume,
                    min_market_cap=min_market_cap,
                    max_assets=max_assets,
                )
                universe_symbols = selection["symbols"]

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
