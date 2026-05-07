"""Celery Task — Auto-discover assets for pools with auto_refresh enabled."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from ..tasks.celery_app import celery_app
from ..services.pool_selection import (
    apply_pool_discovery_filters,
    load_market_cap_map,
    load_profile_discovery_thresholds,
)

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _discover_async():
    from ..database import run_db_task
    from ..models.pool import Pool, PoolCoin
    from ..exchange_adapters.gate_adapter import GateAdapter
    from sqlalchemy import select

    logger.info("Auto-discover assets: starting run...")
    total_added = 0
    total_removed = 0
    pools_processed = 0

    # Load eligible pools in a read-only transaction; extract scalar attributes
    # into plain dicts so ORM objects do not become detached after session close.
    async def _load_pools(db):
        result = await db.execute(
            select(Pool).where(
                Pool.is_active == True,
                text("(overrides->>'auto_refresh')::boolean = true"),
            )
        )
        pools = result.scalars().all()
        return [
            {
                "id": p.id,
                "name": p.name,
                "user_id": p.user_id,
                "market_type": p.market_type or "spot",
                "overrides": p.overrides or {},
                "profile_id": p.profile_id,
            }
            for p in pools
        ]

    pool_data_list = await run_db_task(_load_pools, celery=True)

    adapter = GateAdapter(api_key="", api_secret="")

    for pd in pool_data_list:
        # Each pool's DB writes run in its own transaction so a failure for
        # one pool never prevents the others from being committed.
        try:
            market_type = pd["market_type"]
            overrides = pd["overrides"]
            auto_add = overrides.get("auto_add", True)
            auto_remove = overrides.get("auto_remove", False)
            max_assets = int(overrides.get("max_assets", 0))

            # ── Pre-flight: resolve thresholds BEFORE any API calls ───────
            # min_volume must be known here so we can decide whether to
            # fetch the futures ticker volume map (a slow API call) below.
            # Priority: profile thresholds → pool overrides → 0.
            async def _load_thresholds(db, _pd=pd, _overrides=overrides):
                min_vol = 0.0
                min_cap = 0.0
                if _pd["profile_id"]:
                    min_vol, min_cap, _ = await load_profile_discovery_thresholds(
                        db, _pd["profile_id"],
                    )
                if min_vol <= 0:
                    min_vol = float(_overrides.get("min_volume_24h", 0) or 0)
                if min_cap <= 0:
                    min_cap = float(_overrides.get("min_market_cap", 0) or 0)
                return min_vol, min_cap

            min_volume, min_market_cap = await run_db_task(_load_thresholds, celery=True)

            # ── Fetch external data BEFORE opening the write transaction ──
            # (avoids holding a DB connection during slow API calls)
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
                    pd["name"], len(universe_symbols), len(raw_pairs),
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
                        pd["name"], len(universe_symbols),
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
                            pd["name"], e,
                        )

                logger.info(
                    "Pool '%s': spot universe %d assets (from %d raw tickers, "
                    "after leveraged/stablecoin/active filter)",
                    pd["name"], len(universe_symbols), len(raw_tickers),
                )

            # Volume filter (may require another API call for futures)
            if min_volume > 0:
                if market_type == "futures":
                    try:
                        fut_tickers = await adapter._public_get(
                            f"{adapter.FUTURES_BASE}/futures/{adapter.SETTLE}/tickers"
                        )
                        vol_map = {
                            t.get("contract", ""): float(t.get("volume_24h_quote", 0) or 0)
                            for t in fut_tickers
                        }
                    except Exception as e:
                        logger.warning(f"Futures ticker fetch failed for pool {pd['name']}: {e}")
                        vol_map = {}

            # ── Write transaction: DB reads + adds/removes for this pool ──
            # Thresholds (min_volume, min_market_cap) are already resolved in
            # the pre-flight read transaction above; no need to reload them here.
            async def _persist(
                db,
                _pd=pd,
                _market_type=market_type,
                _universe_symbols=universe_symbols,
                _vol_map=vol_map,
                _min_volume=min_volume,
                _min_market_cap=min_market_cap,
                _max_assets=max_assets,
                _auto_add=auto_add,
                _auto_remove=auto_remove,
            ):
                market_cap_map = {}
                if _min_market_cap > 0:
                    market_cap_map = await load_market_cap_map(db, _universe_symbols)

                selection = apply_pool_discovery_filters(
                    _universe_symbols,
                    vol_map=_vol_map,
                    market_cap_map=market_cap_map,
                    min_volume=_min_volume,
                    min_market_cap=_min_market_cap,
                    max_assets=_max_assets,
                )
                selected_symbols = selection["symbols"]

                # Load existing coins
                coins_result = await db.execute(
                    select(PoolCoin).where(PoolCoin.pool_id == _pd["id"])
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

                if _auto_add:
                    to_add = selected_symbols - existing_manual - set(existing_discovered.keys())
                    for symbol in to_add:
                        db.add(PoolCoin(
                            pool_id=_pd["id"],
                            symbol=symbol,
                            market_type=_market_type,
                            is_active=True,
                            origin="discovered",
                            discovered_at=now,
                        ))
                        added += 1

                if _auto_remove:
                    to_remove = set(existing_discovered.keys()) - selected_symbols
                    for symbol in to_remove:
                        await db.delete(existing_discovered[symbol])
                        removed += 1
                # run_db_task auto-commits on successful exit

                return added, removed, len(selected_symbols)

            added, removed, universe_size = await run_db_task(_persist, celery=True)

            logger.info(
                f"Pool '{pd['name']}': +{added} -{removed} assets "
                f"(universe={universe_size})"
            )
            total_added += added
            total_removed += removed
            pools_processed += 1

            # WebSocket notify if enabled
            if overrides.get("notify_on_changes") and (added > 0 or removed > 0):
                try:
                    from ..websocket.scalpyn_ws_server import broadcast_alert
                    await broadcast_alert(
                        str(pd["user_id"]),
                        "DISCOVER_COMPLETE",
                        {
                            "pool_id": str(pd["id"]),
                            "pool_name": pd["name"],
                            "added": added,
                            "removed": removed,
                        },
                    )
                except Exception:
                    pass  # WS not critical path

        except Exception as e:
            logger.error(f"Auto-discover failed for pool '{pd['name']}': {e}")
            continue

    logger.info(
        f"Auto-discover complete: {pools_processed} pools, "
        f"+{total_added} -{total_removed} assets total"
    )
    return f"{pools_processed} pools | +{total_added} -{total_removed}"


@celery_app.task(name="app.tasks.auto_discover_assets.discover")
def discover():
    return _run_async(_discover_async())
