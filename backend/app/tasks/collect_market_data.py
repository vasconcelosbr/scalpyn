"""Celery Task — collect market data from exchanges into TimescaleDB."""

import asyncio
import logging
from datetime import datetime, timezone

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run async code in sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _collect_all_async():
    from ..services.market_data_service import market_data_service
    from ..database import AsyncSessionLocal
    from ..services.config_service import config_service
    from sqlalchemy import text

    logger.info("Starting market data collection...")

    # Get universe config (use system default — no user_id for centralized collection)
    # For centralized collection, we use a broad universe
    symbols = await market_data_service.get_universe_symbols({
        "min_volume_24h": 5_000_000,
        "max_assets": 100,
    })

    if not symbols:
        logger.warning("No symbols to collect data for")
        return 0

    logger.info(f"Collecting data for {len(symbols)} symbols")
    collected = 0

    # Fetch and store OHLCV for each symbol
    async with AsyncSessionLocal() as db:
        for symbol in symbols[:50]:  # Limit to avoid rate limits
            try:
                df = await market_data_service.fetch_ohlcv(symbol, "1h", limit=100)
                if df is None or df.empty:
                    continue

                # Insert latest candle into TimescaleDB
                latest = df.iloc[-1]
                pair = symbol.replace("USDT", "_USDT") if "_" not in symbol else symbol

                await db.execute(text("""
                    INSERT INTO ohlcv (time, symbol, exchange, timeframe, open, high, low, close, volume)
                    VALUES (:time, :symbol, :exchange, :timeframe, :open, :high, :low, :close, :volume)
                    ON CONFLICT DO NOTHING
                """), {
                    "time": latest["time"],
                    "symbol": symbol,
                    "exchange": "gate.io",
                    "timeframe": "1h",
                    "open": float(latest["open"]),
                    "high": float(latest["high"]),
                    "low": float(latest["low"]),
                    "close": float(latest["close"]),
                    "volume": float(latest["volume"]),
                })

                # Update market_metadata
                await db.execute(text("""
                    INSERT INTO market_metadata (symbol, price, price_change_24h, last_updated)
                    VALUES (:symbol, :price, 0, :updated)
                    ON CONFLICT (symbol) DO UPDATE SET
                        price = :price, last_updated = :updated
                """), {
                    "symbol": symbol,
                    "price": float(latest["close"]),
                    "updated": datetime.now(timezone.utc),
                })

                collected += 1
            except Exception as e:
                logger.warning(f"Failed to collect {symbol}: {e}")
                continue

        # Also fetch tickers for metadata
        try:
            tickers = await market_data_service.fetch_all_tickers()
            for ticker in tickers[:200]:
                pair = ticker.get("currency_pair", "")
                if not pair.endswith("_USDT"):
                    continue
                symbol = pair.replace("_", "")
                price = float(ticker.get("last", 0) or 0)
                change = float(ticker.get("change_percentage", 0) or 0)
                volume = float(ticker.get("quote_volume", 0) or 0)

                if price > 0:
                    await db.execute(text("""
                        INSERT INTO market_metadata (symbol, price, price_change_24h, volume_24h, last_updated)
                        VALUES (:symbol, :price, :change, :volume, :updated)
                        ON CONFLICT (symbol) DO UPDATE SET
                            price = :price, price_change_24h = :change,
                            volume_24h = :volume, last_updated = :updated
                    """), {
                        "symbol": symbol,
                        "price": price,
                        "change": change,
                        "volume": volume,
                        "updated": datetime.now(timezone.utc),
                    })
        except Exception as e:
            logger.warning(f"Failed to update metadata: {e}")

        await db.commit()

    logger.info(f"Market data collection complete: {collected} symbols")
    return collected


@celery_app.task(name="app.tasks.collect_market_data.collect_all")
def collect_all():
    count = _run_async(_collect_all_async())
    # Chain to compute indicators
    celery_app.send_task("app.tasks.compute_indicators.compute")
    return f"Collected {count} symbols"
