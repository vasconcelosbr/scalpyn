"""Asset Search API — search Gate.io assets with price, market cap, and pool membership."""

import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from ..database import get_db
from ..models.pool import Pool, PoolCoin
from ..utils.symbol_filters import is_excluded_asset
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assets", tags=["Asset Search"])

GATE_IO_SPOT_URL = "https://api.gateio.ws/api/v4/spot/tickers"
GATE_IO_FUTURES_URL = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"


async def _fetch_coingecko_market_caps() -> Dict[str, float]:
    """Fetch market cap data for top 250 coins from CoinGecko."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                COINGECKO_MARKETS_URL,
                params={
                    "vs_currency": "usd",
                    "per_page": 250,
                    "page": 1,
                    "order": "market_cap_desc",
                },
            )
            resp.raise_for_status()
            coins = resp.json()
            return {
                c["symbol"].upper(): c.get("market_cap", 0) or 0
                for c in coins
                if c.get("market_cap")
            }
    except Exception as e:
        logger.warning("Failed to fetch market caps from CoinGecko: %s", e)
        return {}


async def _fetch_spot_assets(query: str) -> List[Dict[str, Any]]:
    """Fetch spot tickers from Gate.io, filter by query, enrich with market cap."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(GATE_IO_SPOT_URL)
        resp.raise_for_status()
        tickers = resp.json()

    market_caps = await _fetch_coingecko_market_caps()
    q = query.upper().strip()

    results: List[Dict[str, Any]] = []
    for t in tickers:
        pair = t.get("currency_pair", "")
        if not pair.endswith("_USDT"):
            continue
        if is_excluded_asset(pair):
            continue
        last = float(t.get("last", 0) or 0)
        if last <= 0:
            continue

        base = pair.replace("_USDT", "")
        # Filter by query (prefix or substring match on base or full pair)
        if q and q not in base and q not in pair:
            continue

        mcap = market_caps.get(base, 0)
        results.append({
            "symbol": pair,
            "name": base,
            "price": last,
            "market_cap": mcap,
            "volume_24h": float(t.get("quote_volume", 0) or 0),
            "change_24h": float(t.get("change_percentage", 0) or 0),
            "type": "spot",
        })

    # Sort by market cap descending (assets with 0 mcap go last)
    results.sort(key=lambda x: (x["market_cap"] <= 0, -x["market_cap"]))
    return results


async def _fetch_futures_assets(query: str) -> List[Dict[str, Any]]:
    """Fetch futures tickers from Gate.io, filter by query, enrich with market cap."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(GATE_IO_FUTURES_URL)
        resp.raise_for_status()
        tickers = resp.json()

    market_caps = await _fetch_coingecko_market_caps()
    q = query.upper().strip()

    results: List[Dict[str, Any]] = []
    for t in tickers:
        contract = t.get("contract", "")
        if not contract:
            continue
        last = float(t.get("last", 0) or 0)
        if last <= 0:
            continue

        base = contract.replace("_USDT", "")
        if q and q not in base and q not in contract:
            continue

        mcap = market_caps.get(base, 0)
        results.append({
            "symbol": contract,
            "name": base,
            "price": last,
            "market_cap": mcap,
            "volume_24h": float(t.get("volume_24h_settle", 0) or 0),
            "change_24h": float(t.get("change_percentage", 0) or 0),
            "type": "futures",
        })

    results.sort(key=lambda x: (x["market_cap"] <= 0, -x["market_cap"]))
    return results


@router.get("/search")
async def search_assets(
    query: str = Query(default="", description="Search query (e.g. 'BTC', 'ETH')"),
    type: str = Query(default="spot", description="Market type: 'spot', 'futures', or 'tradfi'"),
    pool_id: Optional[str] = Query(default=None, description="Pool ID to check membership"),
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(default=50, ge=1, le=100, description="Results per page"),
    _user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Search assets from Gate.io with price, market cap, volume.
    Returns paginated results with `already_in_pool` flag when pool_id is provided.
    """
    # Fetch assets based on market type
    if type == "futures":
        all_assets = await _fetch_futures_assets(query)
    elif type == "tradfi":
        # TradFi not available on Gate.io — return empty for now
        all_assets = []
    else:
        all_assets = await _fetch_spot_assets(query)

    # Look up existing pool symbols if pool_id provided
    pool_symbols: set[str] = set()
    if pool_id:
        try:
            pool_uuid = UUID(pool_id)
            coins_result = await db.execute(
                select(PoolCoin.symbol).where(PoolCoin.pool_id == pool_uuid)
            )
            pool_symbols = {row[0] for row in coins_result.all()}
        except (ValueError, Exception) as e:
            logger.warning("Failed to load pool coins for %s: %s", pool_id, e)

    # Tag each asset with already_in_pool
    for asset in all_assets:
        asset["already_in_pool"] = asset["symbol"] in pool_symbols

    total = len(all_assets)

    # Paginate
    start = (page - 1) * per_page
    end = start + per_page
    paginated = all_assets[start:end]

    return {
        "results": paginated,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total > 0 else 0,
        "query": query,
        "type": type,
    }
