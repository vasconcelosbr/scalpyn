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

        # Also fetch tickers for metadata (price, volume, change + spread_pct from bid/ask)
        try:
            tickers = await market_data_service.fetch_all_tickers()
            now_ts = datetime.now(timezone.utc)
            for ticker in tickers[:500]:
                pair = ticker.get("currency_pair", "")
                if not pair.endswith("_USDT"):
                    continue
                symbol = pair  # keep BTC_USDT format (with underscore)
                price = float(ticker.get("last", 0) or 0)
                change = float(ticker.get("change_percentage", 0) or 0)
                volume = float(ticker.get("quote_volume", 0) or 0)
                # Compute spread from tickers bid/ask (no extra API call needed)
                spread = market_data_service.compute_spread_from_ticker(ticker)

                if price > 0:
                    await db.execute(text("""
                        INSERT INTO market_metadata
                            (symbol, price, price_change_24h, volume_24h, spread_pct, last_updated)
                        VALUES (:symbol, :price, :change, :volume, :spread, :updated)
                        ON CONFLICT (symbol) DO UPDATE SET
                            price = :price, price_change_24h = :change,
                            volume_24h = :volume, spread_pct = :spread, last_updated = :updated
                    """), {
                        "symbol": symbol,
                        "price":  price,
                        "change": change,
                        "volume": volume,
                        "spread": spread,
                        "updated": now_ts,
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


async def _collect_5m_async():
    """Collect 5-minute OHLCV candles for pipeline scan freshness.

    Universe = top-100 high-volume symbols  UNION  all active pool coin symbols.
    This ensures every asset in a user's pipeline pool gets indicator data even
    if its 24h volume is below the universe threshold.
    """
    from ..services.market_data_service import market_data_service
    from ..utils.symbol_filters import filter_real_assets
    from ..database import AsyncSessionLocal
    from sqlalchemy import text

    logger.info("Starting 5m market data collection...")

    universe = await market_data_service.get_universe_symbols({
        "min_volume_24h": 5_000_000,
        "max_assets": 100,
    })

    # Also include all active pool coin symbols so lower-volume assets get data
    async with AsyncSessionLocal() as db:
        pool_rows = (await db.execute(text(
            "SELECT DISTINCT symbol FROM pool_coins WHERE is_active = true"
        ))).fetchall()
    pool_syms = filter_real_assets([r.symbol for r in pool_rows])

    symbols = list(dict.fromkeys(universe + pool_syms))  # deduplicate, preserve order

    if not symbols:
        logger.warning("No symbols for 5m collection")
        return 0

    logger.info("5m collection universe: %d symbols (%d from universe, %d from pools)",
                len(symbols), len(universe), len(pool_syms))

    collected = 0
    async with AsyncSessionLocal() as db:
        for symbol in symbols[:500]:  # cap raised to cover large pools
            try:
                df = await market_data_service.fetch_ohlcv(symbol, "5m", limit=100)
                if df is None or df.empty:
                    continue

                # Bulk-insert all returned candles (ON CONFLICT DO NOTHING is idempotent)
                for _, row in df.iterrows():
                    await db.execute(text("""
                        INSERT INTO ohlcv (time, symbol, exchange, timeframe, open, high, low, close, volume)
                        VALUES (:time, :symbol, :exchange, :timeframe, :open, :high, :low, :close, :volume)
                        ON CONFLICT DO NOTHING
                    """), {
                        "time":      row["time"],
                        "symbol":    symbol,
                        "exchange":  "gate.io",
                        "timeframe": "5m",
                        "open":      float(row["open"]),
                        "high":      float(row["high"]),
                        "low":       float(row["low"]),
                        "close":     float(row["close"]),
                        "volume":    float(row["volume"]),
                    })

                # Fetch orderbook metrics (spread + depth) and update market_metadata
                try:
                    ob = await market_data_service.fetch_orderbook_metrics(symbol, depth=10)
                    if ob:
                        await db.execute(text("""
                            INSERT INTO market_metadata (symbol, spread_pct, orderbook_depth_usdt, last_updated)
                            VALUES (:sym, :spread, :depth, :ts)
                            ON CONFLICT (symbol) DO UPDATE SET
                                spread_pct = :spread,
                                orderbook_depth_usdt = :depth,
                                last_updated = :ts
                        """), {
                            "sym":    symbol,
                            "spread": ob.get("spread_pct"),
                            "depth":  ob.get("orderbook_depth_usdt"),
                            "ts":     datetime.now(timezone.utc),
                        })
                except Exception:
                    pass  # non-blocking — orderbook metrics are best-effort

                collected += 1
            except Exception as e:
                logger.warning(f"Failed to collect 5m data for {symbol}: {e}")
                continue

        await db.commit()

    logger.info(f"5m collection complete: {collected} symbols")
    return collected


@celery_app.task(name="app.tasks.collect_market_data.collect_5m")
def collect_5m():
    count = _run_async(_collect_5m_async())
    # Chain: fresh 5m candles → compute 5m indicators → pipeline scan
    celery_app.send_task("app.tasks.compute_indicators.compute_5m")
    return f"Collected 5m data for {count} symbols"
