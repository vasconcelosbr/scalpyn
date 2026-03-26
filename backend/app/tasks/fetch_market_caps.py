"""Celery Task — Fetch market cap data and update DB.

Primary source: Gate.io /spot/currencies (bulk, no API key needed).
Optional enhancement: CoinMarketCap (if key is configured — more accurate data).

Runs every 30 minutes via Celery Beat.
"""

import asyncio
import logging
import re

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

GATE_CURRENCIES_URL = "https://api.gateio.ws/api/v4/spot/currencies"
CMC_LISTINGS_URL    = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
CMC_LIMIT           = 500

# Filter out leveraged ETF tokens (e.g. BTC3L, PEPE5S, TON2L)
_ETF_PATTERN = re.compile(r"\d+[LS]$", re.IGNORECASE)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _fetch_from_gate() -> dict[str, float]:
    """Fetch market caps from Gate.io /spot/currencies — free, bulk, no key needed."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(GATE_CURRENCIES_URL)
            resp.raise_for_status()
            coins = resp.json()

        result = {}
        for coin in coins:
            symbol = coin.get("currency", "").upper()
            if _ETF_PATTERN.search(symbol):
                continue
            if coin.get("trade_disabled") or coin.get("delisted"):
                continue
            mcap_str = coin.get("market_cap", "") or ""
            try:
                mcap = float(mcap_str)
                if mcap > 0:
                    result[symbol] = mcap
            except (ValueError, TypeError):
                pass

        logger.info("Gate.io: fetched market caps for %d coins.", len(result))
        return result
    except Exception as exc:
        logger.error("Failed to fetch market caps from Gate.io: %s", exc)
        return {}


async def _fetch_from_cmc(cmc_key: str) -> dict[str, float]:
    """Fetch market caps from CoinMarketCap — requires API key, more accurate data."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                CMC_LISTINGS_URL,
                headers={"X-CMC_PRO_API_KEY": cmc_key, "Accept": "application/json"},
                params={"start": 1, "limit": CMC_LIMIT, "convert": "USD", "sort": "market_cap"},
            )
            resp.raise_for_status()
            data = resp.json()

        result = {}
        for coin in data.get("data", []):
            symbol = coin.get("symbol", "").upper()
            mcap = coin.get("quote", {}).get("USD", {}).get("market_cap")
            if symbol and mcap:
                result[symbol] = float(mcap)

        logger.info("CoinMarketCap: fetched market caps for %d coins.", len(result))
        return result
    except Exception as exc:
        logger.error("Failed to fetch market caps from CoinMarketCap: %s", exc)
        return {}


async def _fetch_market_caps_async() -> dict:
    from sqlalchemy import select, text
    from ..database import AsyncSessionLocal
    from ..models.ai_provider_key import AIProviderKey
    from ..services.ai_keys_service import decrypt_value

    stats = {"source": "gate.io", "updated_metadata": 0, "updated_pipeline": 0, "error": None}

    async with AsyncSessionLocal() as db:
        # 1. Primary source: Gate.io (always runs)
        market_caps = await _fetch_from_gate()

        # 2. Optional: merge CoinMarketCap data on top (overwrites Gate.io for matched symbols)
        cmc_row_res = await db.execute(
            select(AIProviderKey).where(
                AIProviderKey.provider == "coinmarketcap",
                AIProviderKey.is_active == True,
            ).limit(1)
        )
        cmc_row = cmc_row_res.scalars().first()

        if cmc_row:
            try:
                raw = bytes(cmc_row.api_key_encrypted) if isinstance(cmc_row.api_key_encrypted, memoryview) else cmc_row.api_key_encrypted
                cmc_key = decrypt_value(raw).strip()
                cmc_caps = await _fetch_from_cmc(cmc_key)
                if cmc_caps:
                    market_caps.update(cmc_caps)
                    stats["source"] = "gate.io+coinmarketcap"
                    logger.info("Merged CMC data — final coverage: %d coins.", len(market_caps))
            except Exception as exc:
                logger.warning("CMC merge skipped: %s", exc)

        if not market_caps:
            stats["error"] = "no_data"
            return stats

        # 3. Update market_metadata
        mm_symbols = await _get_distinct_symbols(db, "market_metadata")
        for symbol_pair in mm_symbols:
            base = _base_from_pair(symbol_pair)
            mcap = market_caps.get(base)
            if mcap:
                await db.execute(text(
                    "UPDATE market_metadata SET market_cap = :mcap WHERE symbol = :symbol"
                ), {"mcap": mcap, "symbol": symbol_pair})
                stats["updated_metadata"] += 1

        # 4. Update pipeline_watchlist_assets
        pwa_symbols = await _get_distinct_symbols(db, "pipeline_watchlist_assets")
        for symbol_pair in pwa_symbols:
            base = _base_from_pair(symbol_pair)
            mcap = market_caps.get(base)
            if mcap:
                await db.execute(text(
                    "UPDATE pipeline_watchlist_assets SET market_cap = :mcap WHERE symbol = :symbol"
                ), {"mcap": mcap, "symbol": symbol_pair})
                stats["updated_pipeline"] += 1

        await db.commit()

    logger.info(
        "Market cap update complete — source=%s  metadata=%d  pipeline=%d",
        stats["source"], stats["updated_metadata"], stats["updated_pipeline"],
    )
    return stats


def _base_from_pair(symbol: str) -> str:
    """FARTCOIN_USDT → FARTCOIN,  BTCUSDT → BTC"""
    return symbol.replace("_USDT", "").replace("USDT", "").upper()


async def _get_distinct_symbols(db, table: str) -> list[str]:
    from sqlalchemy import text
    res = await db.execute(text(f"SELECT DISTINCT symbol FROM {table}"))
    return [r[0] for r in res.fetchall()]


@celery_app.task(name="app.tasks.fetch_market_caps.fetch_market_caps", bind=True, max_retries=0)
def fetch_market_caps(self):
    """Fetch market caps (Gate.io primary + CMC optional) and update DB every 30 min."""
    logger.info("Starting market cap fetch...")
    try:
        result = _run_async(_fetch_market_caps_async())
        logger.info("Market cap fetch result: %s", result)
        return result
    except Exception as exc:
        logger.exception("Market cap fetch failed: %s", exc)
        raise
