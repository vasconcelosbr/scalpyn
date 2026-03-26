"""Celery Task — Fetch market cap data from CoinMarketCap and update DB.

Runs every 30 minutes via Celery Beat.
Uses the CoinMarketCap API key stored per-user in ai_provider_keys
(provider = "coinmarketcap"). Uses the first available valid key.
"""

import asyncio
import logging

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

CMC_LISTINGS_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
CMC_CONVERT = "USD"
CMC_LIMIT = 500


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _fetch_market_caps_async() -> dict:
    import httpx
    from sqlalchemy import select, text
    from ..database import AsyncSessionLocal
    from ..models.ai_provider_key import AIProviderKey
    from ..services.ai_keys_service import decrypt_value

    stats = {"updated_metadata": 0, "updated_pipeline": 0, "error": None}

    async with AsyncSessionLocal() as db:
        # 1. Get any active CMC key (market cap is global — first key wins)
        row_res = await db.execute(
            select(AIProviderKey).where(
                AIProviderKey.provider == "coinmarketcap",
                AIProviderKey.is_active == True,
            ).limit(1)
        )
        key_row = row_res.scalars().first()
        if not key_row:
            logger.info("No CoinMarketCap API key configured — skipping market cap update.")
            stats["error"] = "no_key"
            return stats

        try:
            raw = bytes(key_row.api_key_encrypted) if isinstance(key_row.api_key_encrypted, memoryview) else key_row.api_key_encrypted
            cmc_key = decrypt_value(raw).strip()
        except Exception as exc:
            logger.error("Failed to decrypt CMC key: %s", exc)
            stats["error"] = "decrypt_error"
            return stats

        # 2. Fetch top N coins from CoinMarketCap
        market_caps: dict[str, float] = {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    CMC_LISTINGS_URL,
                    headers={"X-CMC_PRO_API_KEY": cmc_key, "Accept": "application/json"},
                    params={
                        "start": 1,
                        "limit": CMC_LIMIT,
                        "convert": CMC_CONVERT,
                        "sort": "market_cap",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            for coin in data.get("data", []):
                symbol = coin.get("symbol", "").upper()
                mcap = coin.get("quote", {}).get(CMC_CONVERT, {}).get("market_cap")
                if symbol and mcap:
                    market_caps[symbol] = float(mcap)

            logger.info("CMC: fetched market caps for %d coins.", len(market_caps))
        except Exception as exc:
            logger.error("Failed to fetch data from CoinMarketCap: %s", exc)
            stats["error"] = str(exc)
            return stats

        if not market_caps:
            return stats

        # 3. Update market_metadata
        for symbol_usdt in await _get_all_symbols(db, "market_metadata"):
            base = symbol_usdt.replace("_USDT", "").replace("USDT", "")
            mcap = market_caps.get(base.upper())
            if mcap:
                await db.execute(text("""
                    UPDATE market_metadata SET market_cap = :mcap
                    WHERE symbol = :symbol
                """), {"mcap": mcap, "symbol": symbol_usdt})
                stats["updated_metadata"] += 1

        # 4. Update pipeline_watchlist_assets
        for symbol_usdt in await _get_all_symbols(db, "pipeline_watchlist_assets"):
            base = symbol_usdt.replace("_USDT", "").replace("USDT", "")
            mcap = market_caps.get(base.upper())
            if mcap:
                await db.execute(text("""
                    UPDATE pipeline_watchlist_assets SET market_cap = :mcap
                    WHERE symbol = :symbol
                """), {"mcap": mcap, "symbol": symbol_usdt})
                stats["updated_pipeline"] += 1

        await db.commit()

    logger.info(
        "Market cap update complete — metadata=%d  pipeline=%d",
        stats["updated_metadata"], stats["updated_pipeline"],
    )
    return stats


async def _get_all_symbols(db, table: str) -> list[str]:
    from sqlalchemy import text
    res = await db.execute(text(f"SELECT DISTINCT symbol FROM {table}"))
    return [r[0] for r in res.fetchall()]


@celery_app.task(name="app.tasks.fetch_market_caps.fetch_market_caps", bind=True, max_retries=0)
def fetch_market_caps(self):
    """Fetch market caps from CoinMarketCap and update market_metadata + pipeline_watchlist_assets."""
    logger.info("Starting market cap fetch from CoinMarketCap...")
    try:
        result = _run_async(_fetch_market_caps_async())
        logger.info("Market cap fetch result: %s", result)
        return result
    except Exception as exc:
        logger.exception("Market cap fetch failed: %s", exc)
        raise
