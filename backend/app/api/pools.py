import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Dict, Any, List
from uuid import UUID

from ..database import get_db
from ..models.pool import Pool, PoolCoin
from .config import get_current_user_id
from ..services.pool_selection import (
    apply_pool_discovery_filters,
    extract_profile_discovery_thresholds,
    load_market_cap_map,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pools", tags=["Pools"])


def _pool_to_dict(pool: Pool, asset_count: int = 0) -> Dict[str, Any]:
    return {
        "id": str(pool.id),
        "name": pool.name,
        "description": pool.description,
        "is_active": pool.is_active,
        "mode": pool.mode,
        "market_type": pool.market_type,
        "profile_id": str(pool.profile_id) if pool.profile_id else None,
        "overrides": pool.overrides if pool.overrides else {},
        "autopilot_enabled": getattr(pool, "autopilot_enabled", False) or False,
        "asset_count": asset_count,
        "created_at": pool.created_at.isoformat() if pool.created_at else None,
        "updated_at": pool.updated_at.isoformat() if pool.updated_at else None,
    }


def _coin_to_dict(coin: PoolCoin) -> Dict[str, Any]:
    return {
        "id": str(coin.id),
        "pool_id": str(coin.pool_id),
        "symbol": coin.symbol,
        "market_type": coin.market_type,
        "is_active": coin.is_active,
        "added_at": coin.added_at.isoformat() if coin.added_at else None,
        "origin": coin.origin if coin.origin else "manual",
        "discovered_at": coin.discovered_at.isoformat() if coin.discovered_at else None,
    }


@router.get("/")
async def get_pools(db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(Pool).where(Pool.user_id == user_id)
    result = await db.execute(query)
    pools = result.scalars().all()

    pool_list = []
    for pool in pools:
        count_result = await db.execute(
            select(func.count(PoolCoin.id)).where(
                PoolCoin.pool_id == pool.id,
                PoolCoin.is_active == True,
            )
        )
        asset_count = count_result.scalar() or 0
        pool_list.append(_pool_to_dict(pool, asset_count=asset_count))

    return {"pools": pool_list}


@router.post("/")
async def create_pool(payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    # Parse profile_id if provided
    profile_id = payload.get("profile_id")
    if profile_id and isinstance(profile_id, str):
        from uuid import UUID as UUIDType
        try:
            profile_id = UUIDType(profile_id)
        except ValueError:
            profile_id = None
    
    pool = Pool(
        user_id=user_id,
        name=payload.get("name"),
        description=payload.get("description", ""),
        is_active=payload.get("is_active", True),
        mode=payload.get("mode", "paper"),
        market_type=payload.get("market_type", "spot"),
        profile_id=profile_id,
        overrides=payload.get("overrides", {}),
    )
    db.add(pool)
    await db.commit()
    await db.refresh(pool)
    return _pool_to_dict(pool)


@router.delete("/{pool_id}")
async def delete_pool(pool_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    result = await db.execute(query)
    pool = result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    await db.delete(pool)
    await db.commit()
    return {"status": "success", "message": "Pool deleted"}


@router.patch("/{pool_id}")
async def update_pool(pool_id: UUID, payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    result = await db.execute(query)
    pool = result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    if "name" in payload:
        pool.name = payload["name"]
    if "description" in payload:
        pool.description = payload["description"]
    if "is_active" in payload:
        pool.is_active = payload["is_active"]
    if "mode" in payload:
        pool.mode = payload["mode"]
    if "market_type" in payload:
        pool.market_type = payload["market_type"]
    if "profile_id" in payload:
        profile_id = payload["profile_id"]
        if profile_id and isinstance(profile_id, str):
            from uuid import UUID as UUIDType
            try:
                pool.profile_id = UUIDType(profile_id)
            except ValueError:
                pool.profile_id = None
        else:
            pool.profile_id = profile_id
    if "overrides" in payload:
        pool.overrides = payload["overrides"]

    await db.commit()
    await db.refresh(pool)
    return _pool_to_dict(pool)


@router.get("/{pool_id}/coins")
async def get_pool_coins(pool_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    if not pool_result.scalars().first():
        raise HTTPException(status_code=404, detail="Pool not found")

    query = select(PoolCoin).where(PoolCoin.pool_id == pool_id)
    result = await db.execute(query)
    coins = result.scalars().all()
    return {"coins": [_coin_to_dict(c) for c in coins]}


@router.post("/{pool_id}/coins")
async def add_pool_coin(pool_id: UUID, payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    if not pool_result.scalars().first():
        raise HTTPException(status_code=404, detail="Pool not found")

    symbol = payload.get("symbol", "").upper().strip()
    market_type = payload.get("market_type", "spot").lower()

    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")

    # Normalize symbol to Gate.io format with underscore (e.g. BTCUSDT → BTC_USDT)
    # This ensures consistency with market_metadata which uses BTC_USDT format.
    if "_" not in symbol and symbol.endswith("USDT"):
        symbol = symbol[:-4] + "_USDT"

    existing_query = select(PoolCoin).where(PoolCoin.pool_id == pool_id, PoolCoin.symbol == symbol)
    existing_result = await db.execute(existing_query)
    if existing_result.scalars().first():
        raise HTTPException(status_code=409, detail="Symbol already in pool")

    coin = PoolCoin(pool_id=pool_id, symbol=symbol, market_type=market_type, is_active=True)
    db.add(coin)
    await db.commit()
    await db.refresh(coin)
    return _coin_to_dict(coin)


@router.post("/{pool_id}/coins/bulk")
async def bulk_add_pool_coins(
    pool_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Bulk insert multiple assets into a pool.

    Payload: { "assets": [{ "symbol": "BTC_USDT", "market_type": "spot" }, ...] }

    - Skips duplicates (symbols already in pool)
    - Normalizes symbols (uppercase, underscore format)
    - Validates each symbol is non-empty
    - Ensures market_metadata row exists for each added symbol
    """
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    assets: List[Dict[str, str]] = payload.get("assets", [])
    if not assets:
        raise HTTPException(status_code=400, detail="No assets provided")
    if len(assets) > 200:
        raise HTTPException(status_code=400, detail="Maximum 200 assets per request")

    # Load existing pool symbols for dedup
    existing_result = await db.execute(
        select(PoolCoin.symbol).where(PoolCoin.pool_id == pool_id)
    )
    existing_symbols: set[str] = {row[0] for row in existing_result.all()}

    added: List[str] = []
    skipped: List[str] = []
    new_coins: List[PoolCoin] = []

    for item in assets:
        symbol = (item.get("symbol") or "").upper().strip()
        market_type = (item.get("market_type") or "spot").lower()

        if not symbol:
            continue

        # Normalize symbol to Gate.io format (BTCUSDT → BTC_USDT)
        if "_" not in symbol and symbol.endswith("USDT"):
            symbol = symbol[:-4] + "_USDT"

        if symbol in existing_symbols:
            skipped.append(symbol)
            continue

        new_coins.append(PoolCoin(
            pool_id=pool_id,
            symbol=symbol,
            market_type=market_type,
            is_active=True,
            origin="manual",
        ))
        existing_symbols.add(symbol)
        added.append(symbol)

    # Bulk insert all new coins at once
    if new_coins:
        db.add_all(new_coins)

    # Ensure market_metadata rows exist for newly added symbols
    if added:
        from sqlalchemy import text as sa_text

        for sym in added:
            try:
                await db.execute(
                    sa_text(
                        "INSERT INTO market_metadata (symbol) VALUES (:sym) "
                        "ON CONFLICT (symbol) DO NOTHING"
                    ),
                    {"sym": sym},
                )
            except Exception as e:
                logger.warning("Failed to upsert market_metadata for %s: %s", sym, e)

    await db.commit()

    return {
        "status": "success",
        "added": len(added),
        "skipped": len(skipped),
        "added_symbols": added,
        "skipped_symbols": skipped,
    }


@router.delete("/{pool_id}/coins/{symbol}")
async def remove_pool_coin(pool_id: UUID, symbol: str, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    if not pool_result.scalars().first():
        raise HTTPException(status_code=404, detail="Pool not found")

    coin_query = select(PoolCoin).where(PoolCoin.pool_id == pool_id, PoolCoin.symbol == symbol.upper())
    coin_result = await db.execute(coin_query)
    coin = coin_result.scalars().first()
    if not coin:
        raise HTTPException(status_code=404, detail="Symbol not found in pool")

    await db.delete(coin)
    await db.commit()
    return {"status": "success", "message": f"{symbol} removed from pool"}


@router.get("/{pool_id}/overrides")
async def get_pool_overrides(pool_id: UUID, db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    overrides = pool.overrides if pool.overrides else {}
    return {"pool_id": str(pool_id), "overrides": overrides}


@router.put("/{pool_id}/overrides")
async def update_pool_overrides(pool_id: UUID, payload: Dict[str, Any], db: AsyncSession = Depends(get_db), user_id: UUID = Depends(get_current_user_id)):
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    pool.overrides = payload
    await db.commit()
    await db.refresh(pool)
    return {"pool_id": str(pool_id), "overrides": pool.overrides}


@router.post("/{pool_id}/discover")
async def discover_pool_assets(
    pool_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Auto-discover assets for a pool from Gate.io universe.

    1. Load pool + linked Strategy Profile
    2. Fetch all tradable pairs from Gate.io public API
    3. Apply filters exclusively from linked profile (no hardcoded overrides)
    4. Compare with existing pool_coins
    5. Insert new coins with origin='discovered', remove stale discovered ones
    6. Return { found, added, removed, kept_manual, profile_applied }
    """
    from ..exchange_adapters.gate_adapter import GateAdapter
    from ..models.profile import Profile

    # ── 1. Load pool ──────────────────────────────────────────────────────────
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    market_type = pool.market_type or "spot"
    overrides = pool.overrides or {}
    max_assets = int(overrides.get("max_assets", 0))

    # Filters come exclusively from linked Strategy Profile
    min_volume = 0.0
    min_market_cap = 0.0
    profile_applied = False

    if pool.profile_id:
        profile_query = select(Profile).where(Profile.id == pool.profile_id)
        profile_result = await db.execute(profile_query)
        profile = profile_result.scalars().first()
        if profile:
            logger.info(f"[Discovery] Using profile {profile.id} ({profile.name}) filters for pool {pool_id}")
        min_volume, min_market_cap, profile_applied = extract_profile_discovery_thresholds(
            profile.config if profile else None,
        )
        logger.info(f"[Discovery] Profile filters: min_volume={min_volume}, min_market_cap={min_market_cap}")

    # ── 2. Fetch universe from Gate.io (public endpoints) ─────────────────────
    from ..utils.symbol_filters import is_excluded_asset, is_leveraged_token

    adapter = GateAdapter(api_key="", api_secret="")
    vol_map: dict[str, float] = {}
    try:
        if market_type == "futures":
            raw_pairs = await adapter.list_futures_contracts()
            # Build symbol set from contract names — exclude leveraged tokens
            universe_symbols: set[str] = {
                p["name"] for p in raw_pairs
                if not is_leveraged_token(p["name"])
            }
            logger.info(
                "[Discovery] Gate.io futures universe for pool %s: %d contracts "
                "(from %d raw, after leveraged filter)",
                pool_id, len(universe_symbols), len(raw_pairs),
            )
        else:
            # Primary source: tickers (public, no auth) — only active pairs
            # have tickers, so this naturally filters non-tradable pairs.
            raw_tickers = await adapter.list_spot_tickers_public()
            logger.info(
                "[Discovery] Gate.io returned %d spot tickers", len(raw_tickers),
            )
            universe_symbols = set()
            for t in raw_tickers:
                pair = t.get("currency_pair", "")
                if not pair.endswith("_USDT"):
                    continue
                if is_excluded_asset(pair):
                    continue
                # Exclude stale/dead pairs with no last price
                last = float(t.get("last", 0) or 0)
                if last <= 0:
                    continue
                universe_symbols.add(pair)
                # Pre-build volume map (avoids a separate ticker fetch)
                vol_map[pair] = float(t.get("quote_volume", 0) or 0)

            logger.info(
                "[Discovery] Gate.io spot universe for pool %s: %d USDT pairs "
                "(from %d raw tickers, after leveraged/stablecoin/active filter)",
                pool_id, len(universe_symbols), len(raw_tickers),
            )

            # Fallback: if tickers returned suspiciously few results,
            # also try currency_pairs endpoint and merge
            if len(universe_symbols) < 200:
                logger.warning(
                    "[Discovery] Ticker-based universe unexpectedly small (%d), "
                    "supplementing with currency_pairs endpoint",
                    len(universe_symbols),
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
                    logger.info(
                        "[Discovery] After currency_pairs supplement: %d assets "
                        "(from %d raw pairs)",
                        len(universe_symbols), len(raw_pairs),
                    )
                except Exception as e:
                    logger.warning(
                        "[Discovery] currency_pairs fallback failed: %s", e,
                    )
    except Exception as e:
        logger.error(f"Gate.io discovery failed for pool {pool_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Gate.io API error: {e}")

    # ── 3. Apply pool selection filters ───────────────────────────────────────
    if min_volume > 0:
        # For futures, need a separate ticker fetch; for spot vol_map is pre-built
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
                logger.warning(f"Futures ticker fetch failed, skipping volume filter: {e}")
                vol_map = {}

    market_cap_map = {}
    if min_market_cap > 0:
        market_cap_map = await load_market_cap_map(db, universe_symbols)
        if not market_cap_map:
            logger.warning(
                "[Discovery] market cap filter configured (>= $%s) but market_metadata has no coverage for pool %s",
                f"{min_market_cap:,.0f}",
                pool_id,
            )

    selection = apply_pool_discovery_filters(
        universe_symbols,
        vol_map=vol_map,
        market_cap_map=market_cap_map,
        min_volume=min_volume,
        min_market_cap=min_market_cap,
        max_assets=max_assets,
    )
    universe_symbols = selection["symbols"]

    if min_volume > 0 and vol_map:
        logger.info(
            "[Discovery] Volume filter (>= $%s): %d → %d assets",
            f"{min_volume:,.0f}",
            selection["pre_volume_count"],
            selection["post_volume_count"],
        )
    if min_market_cap > 0 and market_cap_map:
        logger.info(
            "[Discovery] Market cap filter (>= $%s): %d → %d assets",
            f"{min_market_cap:,.0f}",
            selection["pre_market_cap_count"],
            selection["post_market_cap_count"],
        )
    if max_assets > 0 and selection["pre_cap_count"] > len(universe_symbols):
        logger.info(
            "[Discovery] max_assets cap: %d → %d assets",
            selection["pre_cap_count"],
            len(universe_symbols),
        )

    found = len(universe_symbols)
    raw_count = len(raw_tickers) if market_type != "futures" else len(raw_pairs)
    logger.info(
        "[Discovery] Pool %s final discovery: %d assets "
        "(raw=%d, post-filter=%d, post-volume=%d, post-market-cap=%d, post-cap=%d)",
        pool_id,
        found,
        raw_count,
        selection["pre_volume_count"],
        selection["post_volume_count"],
        selection["post_market_cap_count"],
        found,
    )

    # ── 4. Load existing pool coins ───────────────────────────────────────────
    coins_result = await db.execute(
        select(PoolCoin).where(PoolCoin.pool_id == pool_id)
    )
    existing_coins = coins_result.scalars().all()
    existing_manual = {c.symbol for c in existing_coins if (c.origin or "manual") == "manual"}
    existing_discovered = {c.symbol: c for c in existing_coins if (c.origin or "manual") == "discovered"}

    # ── 5. Diff ───────────────────────────────────────────────────────────────
    to_add = universe_symbols - existing_manual - set(existing_discovered.keys())
    to_remove = set(existing_discovered.keys()) - universe_symbols  # stale discovered

    now = datetime.now(timezone.utc)

    # Insert new discovered coins
    for symbol in to_add:
        coin = PoolCoin(
            pool_id=pool_id,
            symbol=symbol,
            market_type=market_type,
            is_active=True,
            origin="discovered",
            discovered_at=now,
        )
        db.add(coin)

    # Remove stale discovered coins (never touch manual ones)
    for symbol, coin_obj in existing_discovered.items():
        if symbol in to_remove:
            await db.delete(coin_obj)

    await db.commit()

    return {
        "found": found,
        "added": len(to_add),
        "removed": len(to_remove),
        "kept_manual": len(existing_manual),
        "profile_applied": profile_applied,
        "filters_used": {
            "source": "profile" if profile_applied else "none",
            "min_volume_24h": min_volume,
            "min_market_cap": min_market_cap,
            "max_assets": max_assets,
        }
    }


# ============================================================================
# PRESET IA PARA POOL
# ============================================================================

@router.post("/{pool_id}/preset-ia")
async def run_pool_preset_ia(
    pool_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Executa Preset IA para o pool.
    Analisa os ativos e sugere critérios de filtro/scoring.
    """
    from ..services.preset_ia_service import run_preset_ia_for_pool

    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    coins_result = await db.execute(
        select(PoolCoin).where(PoolCoin.pool_id == pool_id, PoolCoin.is_active == True)
    )
    coins = coins_result.scalars().all()
    symbols = [c.symbol for c in coins]

    result = await run_preset_ia_for_pool(
        pool_id=str(pool_id),
        pool_name=pool.name,
        symbols=symbols,
        user_id=str(user_id),
        current_overrides=pool.overrides or {},
        db=db,
    )

    return {
        "status": "success",
        "pool_id": str(pool_id),
        **result,
    }


@router.post("/{pool_id}/preset-ia/apply")
async def apply_pool_preset_ia(
    pool_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Aplica as recomendações do Preset IA ao pool.
    Payload: { "recommendations": { "min_volume_24h": ..., "remove_symbols": [...] } }
    """
    from ..services.preset_ia_service import apply_pool_preset_recommendations

    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    recommendations = payload.get("recommendations", {})
    if not recommendations:
        raise HTTPException(status_code=400, detail="recommendations é obrigatório")

    applied = await apply_pool_preset_recommendations(
        pool_id=str(pool_id),
        recommendations=recommendations,
        db=db,
    )

    await db.refresh(pool)
    return {
        "status": "success",
        "pool_id": str(pool_id),
        "pool": _pool_to_dict(pool),
        **applied,
    }


@router.post("/{pool_id}/autopilot/toggle")
async def toggle_pool_autopilot(
    pool_id: UUID,
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Liga/desliga o AutoPilot do pool.
    Payload: { "enabled": true|false }
    """
    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    enabled = payload.get("enabled")
    if enabled is None:
        enabled = not (getattr(pool, "autopilot_enabled", False) or False)

    pool.autopilot_enabled = bool(enabled)
    await db.commit()
    await db.refresh(pool)

    return {
        "status": "success",
        "pool_id": str(pool_id),
        "autopilot_enabled": pool.autopilot_enabled,
        "pool": _pool_to_dict(pool),
    }


@router.post("/{pool_id}/scan")
async def scan_and_populate_pool(
    pool_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Scan market and populate pool with assets.
    Filters come exclusively from linked Strategy Profile.
    max_assets is user-configurable via pool overrides (0 = no limit).
    """
    from ..services.market_data_service import market_data_service
    from ..models.profile import Profile

    pool_query = select(Pool).where(Pool.id == pool_id, Pool.user_id == user_id)
    pool_result = await db.execute(pool_query)
    pool = pool_result.scalars().first()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    overrides = pool.overrides or {}
    max_assets = int(overrides.get("max_assets", 0))

    # Filters come exclusively from linked Strategy Profile
    min_volume = 0.0
    min_market_cap = 0.0
    profile_applied = False

    if pool.profile_id:
        profile_query = select(Profile).where(Profile.id == pool.profile_id)
        profile_result = await db.execute(profile_query)
        profile = profile_result.scalars().first()
        if profile and profile.config:
            logger.info(f"[Scan] Using profile {profile.id} ({profile.name}) filters for pool {pool_id}")

            filters = profile.config.get("filters", {})
            conditions = filters.get("conditions", [])

            for cond in conditions:
                field = cond.get("field", "")
                operator = cond.get("operator", ">")
                value = cond.get("value", 0)

                if field in ["volume_24h", "volume_24h_usd"]:
                    if operator in [">", ">="]:
                        min_volume = max(min_volume, float(value))
                        profile_applied = True
                elif field in ["market_cap", "market_cap_usd"]:
                    if operator in [">", ">="]:
                        min_market_cap = max(min_market_cap, float(value))
                        profile_applied = True

            logger.info(f"[Scan] Profile filters: min_volume={min_volume}, min_market_cap={min_market_cap}")

    # Buscar ativos do mercado com filtros do profile
    try:
        market_assets = await market_data_service.get_market_metadata(
            min_volume=min_volume,
            min_market_cap=min_market_cap,
        )
        market_assets.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)
        # Normalize symbols to BTC_USDT format for market_metadata consistency
        def _norm(s: str) -> str:
            s = s.upper().strip()
            if "_" not in s and s.endswith("USDT"):
                return s[:-4] + "_USDT"
            return s
        # Apply max_assets cap (user-configurable, 0 = no limit)
        if max_assets > 0:
            universe_symbols = {_norm(a["symbol"]) for a in market_assets[:max_assets]}
        else:
            universe_symbols = {_norm(a["symbol"]) for a in market_assets}
    except Exception as e:
        logger.error(f"[Scan] Falha ao buscar mercado: {e}")
        raise HTTPException(status_code=502, detail=f"Falha ao buscar dados de mercado: {e}")

    # Carregar coins existentes
    coins_result = await db.execute(select(PoolCoin).where(PoolCoin.pool_id == pool_id))
    existing_coins = coins_result.scalars().all()
    existing_manual = {c.symbol for c in existing_coins if (c.origin or "manual") == "manual"}
    existing_discovered = {c.symbol: c for c in existing_coins if (c.origin or "manual") == "discovered"}

    to_add = universe_symbols - existing_manual - set(existing_discovered.keys())
    to_remove = set(existing_discovered.keys()) - universe_symbols

    now = datetime.now(timezone.utc)
    market_type = pool.market_type or "spot"

    for symbol in to_add:
        db.add(PoolCoin(
            pool_id=pool_id,
            symbol=symbol,
            market_type=market_type,
            is_active=True,
            origin="discovered",
            discovered_at=now,
        ))

    for symbol, coin_obj in existing_discovered.items():
        if symbol in to_remove:
            await db.delete(coin_obj)

    await db.commit()

    return {
        "status": "success",
        "found": len(universe_symbols),
        "added": len(to_add),
        "removed": len(to_remove),
        "kept_manual": len(existing_manual),
        "profile_applied": profile_applied,
        "filters_used": {
            "source": "profile" if profile_applied else "none",
            "min_volume_24h": min_volume,
            "min_market_cap": min_market_cap,
            "max_assets": max_assets,
        },
    }
