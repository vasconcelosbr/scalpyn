"""Celery Task — fetch market cap data and update DB every 30 minutes."""

import asyncio
import logging

from ..tasks.celery_app import celery_app
from ..services.coinmarketcap_service import fetch_market_caps as fetch_cmc_market_caps

logger = logging.getLogger(__name__)

GATE_CURRENCIES_URL = "https://api.gateio.ws/api/v4/spot/currencies"


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _fetch_from_gate(symbols: set[str] | None = None) -> dict[str, float]:
    """Fetch fallback market caps from Gate.io /spot/currencies."""
    import httpx

    requested = {symbol.upper() for symbol in (symbols or set()) if symbol}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(GATE_CURRENCIES_URL)
            resp.raise_for_status()
            coins = resp.json()

        result = {}
        for coin in coins:
            symbol = coin.get("currency", "").upper()
            if requested and symbol not in requested:
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

        logger.info("Gate.io fallback: fetched market caps for %d coins.", len(result))
        return result
    except Exception as exc:
        logger.error("Failed to fetch fallback market caps from Gate.io: %s", exc)
        return {}


async def _fetch_market_caps_async() -> dict:
    from sqlalchemy import select, text
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..models.ai_provider_key import AIProviderKey
    from ..services.ai_keys_service import decrypt_value

    stats = {
        "source": "coinmarketcap",
        "updated_metadata": 0,
        "updated_pipeline": 0,
        "error": None,
        "warning": None,
    }

    async with AsyncSessionLocal() as db:
        mm_symbols = await _get_distinct_symbols(db, "market_metadata")
        pwa_symbols = await _get_distinct_symbols(db, "pipeline_watchlist_assets")
        all_pairs = sorted(set(mm_symbols) | set(pwa_symbols))
        requested_bases = sorted({_base_from_pair(symbol_pair) for symbol_pair in all_pairs if symbol_pair})

        cmc_row_res = await db.execute(
            select(AIProviderKey).where(
                AIProviderKey.provider == "coinmarketcap",
                AIProviderKey.is_active == True,
            ).limit(1)
        )
        cmc_row = cmc_row_res.scalars().first()

        market_caps: dict[str, float] = {}
        if cmc_row:
            try:
                raw = bytes(cmc_row.api_key_encrypted) if isinstance(cmc_row.api_key_encrypted, memoryview) else cmc_row.api_key_encrypted
                cmc_key = decrypt_value(raw).strip()
                market_caps = await fetch_cmc_market_caps(requested_bases, cmc_key)
            except Exception as exc:
                stats["warning"] = "cmc_fetch_failed"
                logger.error("Failed to fetch market caps from CoinMarketCap: %s", exc)
        else:
            stats["warning"] = "cmc_key_missing"
            logger.warning("CoinMarketCap key not configured; using Gate.io fallback only.")

        missing_bases = {base for base in requested_bases if base not in market_caps}
        if missing_bases:
            gate_caps = await _fetch_from_gate(missing_bases)
            if gate_caps:
                market_caps.update(gate_caps)
                stats["source"] = "gate.io" if not cmc_row else "coinmarketcap+gate.io-fallback"
                logger.info(
                    "Gate.io fallback filled %d/%d missing market caps.",
                    len([base for base in missing_bases if base in gate_caps]),
                    len(missing_bases),
                )

        if not market_caps:
            stats["error"] = "no_data"
            return stats

        # 3. Update market_metadata
        for symbol_pair in mm_symbols:
            base = _base_from_pair(symbol_pair)
            mcap = market_caps.get(base)
            if mcap:
                await db.execute(text(
                    "UPDATE market_metadata SET market_cap = :mcap WHERE symbol = :symbol"
                ), {"mcap": mcap, "symbol": symbol_pair})
                stats["updated_metadata"] += 1

        # 4. Update pipeline_watchlist_assets
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
    """Fetch market caps (CMC primary with targeted Gate fallback) and update DB every 30 min."""
    logger.info("Starting market cap fetch...")
    try:
        result = _run_async(_fetch_market_caps_async())
        logger.info("Market cap fetch result: %s", result)
        return result
    except Exception as exc:
        logger.exception("Market cap fetch failed: %s", exc)
        raise
