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
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
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
        try:
            for symbol in symbols[:50]:  # Limit to avoid rate limits
                try:
                    df = await market_data_service.fetch_ohlcv(symbol, "1h", limit=100)
                    if df is None or df.empty:
                        continue

                    ohlcv_exchange = df.attrs.get("exchange", "gate.io")
                    for _, row in df.iterrows():
                        await db.execute(text("""
                            INSERT INTO ohlcv (time, symbol, exchange, timeframe, open, high, low, close, volume, quote_volume)
                            VALUES (:time, :symbol, :exchange, :timeframe, :open, :high, :low, :close, :volume, :quote_volume)
                            ON CONFLICT DO NOTHING
                        """), {
                            "time": row["time"],
                            "symbol": symbol,
                            "exchange": ohlcv_exchange,
                            "timeframe": "1h",
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": float(row["volume"]),
                            "quote_volume": float(row.get("quote_volume", float(row["close"]) * float(row["volume"]))),
                        })

                    latest = df.iloc[-1]

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
                    await db.rollback()
                    continue

            # Also fetch tickers for metadata (price, volume, change + spread_pct from bid/ask)
            # Process ALL tickers (not capped) so every pool coin gets market data,
            # even niche coins beyond the top-500.
            try:
                tickers = await market_data_service.fetch_all_tickers()
                if not tickers:
                    logger.warning("fetch_all_tickers returned empty — retrying once after 3 s…")
                    await asyncio.sleep(3)
                    tickers = await market_data_service.fetch_all_tickers()

                now_ts = datetime.now(timezone.utc)
                ticker_ok = 0
                for ticker in tickers:
                    try:
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
                                    (symbol, price, price_change_24h, volume_24h,
                                     spread_pct, last_updated, volume_24h_updated_at)
                                VALUES (:symbol, :price, :change, :volume, :spread,
                                        :updated, :updated)
                                ON CONFLICT (symbol) DO UPDATE SET
                                    price = :price, price_change_24h = :change,
                                    volume_24h = :volume, spread_pct = :spread,
                                    last_updated = :updated,
                                    volume_24h_updated_at = :updated
                            """), {
                                "symbol": symbol,
                                "price":  price,
                                "change": change,
                                "volume": volume,
                                "spread": spread,
                                "updated": now_ts,
                            })
                            ticker_ok += 1
                    except Exception as te:
                        logger.debug("Ticker metadata upsert failed for %s: %s",
                                     ticker.get("currency_pair", "?"), te)
                        await db.rollback()
                        continue

                if ticker_ok:
                    logger.info("market_metadata: upserted %d/%d tickers", ticker_ok, len(tickers))
                else:
                    logger.error(
                        "market_metadata: 0 tickers upserted (fetched %d) — "
                        "collect_5m backup pathway will provide fallback metadata.",
                        len(tickers),
                    )
            except Exception as e:
                logger.error("Failed to fetch/update metadata from tickers: %s", e)
                await db.rollback()

            await db.commit()
        except Exception as e:
            logger.error("Market data collection failed: %s", e)
            await db.rollback()
            raise

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
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
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
    # Normalize pool symbols to BTC_USDT format (market_metadata uses underscores)
    def _norm_sym(s: str) -> str:
        s = s.upper().strip()
        if "_" not in s and s.endswith("USDT"):
            return s[:-4] + "_USDT"
        return s
    pool_syms = filter_real_assets([_norm_sym(r.symbol) for r in pool_rows])

    symbols = list(dict.fromkeys(universe + pool_syms))  # deduplicate, preserve order

    if not symbols:
        logger.warning("No symbols for 5m collection")
        return 0

    logger.info("5m collection universe: %d symbols (%d from universe, %d from pools)",
                len(symbols), len(universe), len(pool_syms))

    collected = 0
    async with AsyncSessionLocal() as db:
        try:
            for symbol in symbols:  # no cap — process all pool symbols
                try:
                    df = await market_data_service.fetch_ohlcv(symbol, "5m", limit=288)
                    if df is None or df.empty:
                        continue

                    ohlcv_exchange = df.attrs.get("exchange", "gate.io")
                    # Bulk-insert all returned candles (ON CONFLICT DO NOTHING is idempotent)
                    for _, row in df.iterrows():
                        await db.execute(text("""
                            INSERT INTO ohlcv (time, symbol, exchange, timeframe, open, high, low, close, volume, quote_volume)
                            VALUES (:time, :symbol, :exchange, :timeframe, :open, :high, :low, :close, :volume, :quote_volume)
                            ON CONFLICT DO NOTHING
                        """), {
                            "time":      row["time"],
                            "symbol":    symbol,
                            "exchange":  ohlcv_exchange,
                            "timeframe": "5m",
                            "open":      float(row["open"]),
                            "high":      float(row["high"]),
                            "low":       float(row["low"]),
                            "close":     float(row["close"]),
                            "volume":    float(row["volume"]),
                            "quote_volume": float(row.get("quote_volume", float(row["close"]) * float(row["volume"]))),
                        })

                    # Seed market_metadata with price from latest OHLCV close.
                    # Ensures every pool coin has a metadata row even when the
                    # tickers-based pathway in collect_all has failed.
                    latest_5m = df.iloc[-1]
                    await db.execute(text("""
                        INSERT INTO market_metadata (symbol, price, last_updated)
                        VALUES (:sym, :price, :ts)
                        ON CONFLICT (symbol) DO UPDATE SET
                            price = :price,
                            last_updated = :ts
                    """), {
                        "sym":   symbol,
                        "price": float(latest_5m["close"]),
                        "ts":    datetime.now(timezone.utc),
                    })

                    # Fetch orderbook metrics (spread + depth) and update market_metadata.
                    # Non-blocking: failure here must never abort 5m OHLCV collection.
                    # fetch_orderbook_metrics internally retries (Gate → Binance fallback)
                    # via resilient_data_service; missing depth lands as NULL in DB, which
                    # the pipeline treats as UNKNOWN (not FAIL) since orderbook_depth_usdt
                    # is no longer in STRICT_META.
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
                        else:
                            logger.warning(
                                "[DATA_UNKNOWN] orderbook metrics unavailable for %s "
                                "(Gate + Binance both failed — depth will be NULL this cycle)",
                                symbol,
                            )
                    except Exception as exc:
                        logger.warning(
                            "[DATA_FAIL] orderbook upsert failed for %s: %s",
                            symbol, exc,
                        )

                    collected += 1
                except Exception as e:
                    logger.warning(f"Failed to collect 5m data for {symbol}: {e}")
                    await db.rollback()
                    continue

            # ── Backup metadata pathway: fetch tickers for volume_24h + spread ───
            # Ensures pool coins get volume_24h populated even when collect_all's
            # tickers block has failed.  Without volume_24h, strict profile filters
            # reject the asset even though a metadata row exists from the OHLCV seed.
            try:
                tickers = await market_data_service.fetch_all_tickers()
                if tickers:
                    now_ts = datetime.now(timezone.utc)
                    ticker_ok = 0
                    for ticker in tickers:
                        try:
                            pair = ticker.get("currency_pair", "")
                            if not pair.endswith("_USDT"):
                                continue
                            price = float(ticker.get("last", 0) or 0)
                            if price <= 0:
                                continue
                            volume = float(ticker.get("quote_volume", 0) or 0)
                            change = float(ticker.get("change_percentage", 0) or 0)
                            spread = market_data_service.compute_spread_from_ticker(ticker)
                            await db.execute(text("""
                                INSERT INTO market_metadata
                                    (symbol, price, price_change_24h, volume_24h,
                                     spread_pct, last_updated, volume_24h_updated_at)
                                VALUES (:symbol, :price, :change, :volume, :spread,
                                        :updated, :updated)
                                ON CONFLICT (symbol) DO UPDATE SET
                                    price = :price,
                                    price_change_24h = :change,
                                    volume_24h = :volume,
                                    spread_pct = :spread,
                                    last_updated = :updated,
                                    volume_24h_updated_at = :updated
                            """), {
                                "symbol": pair,
                                "price":  price,
                                "change": change,
                                "volume": volume,
                                "spread": spread,
                                "updated": now_ts,
                            })
                            ticker_ok += 1
                        except Exception as te:
                            logger.debug("5m: backup ticker upsert failed for %s: %s",
                                         ticker.get("currency_pair", "?"), te)
                            await db.rollback()
                            continue
                    logger.info("5m: backup ticker metadata upserted for %d symbols", ticker_ok)
            except Exception as e:
                logger.debug("5m: backup ticker fetch failed (non-blocking): %s", e)
                await db.rollback()

            # Per-symbol Gate ticker fallback for long-tail pairs missing from the
            # batch endpoint. Staleness is checked against `volume_24h_updated_at`
            # (written only by ticker writers) — NOT `last_updated`, which is
            # touched by price/orderbook seeds in this same task and would
            # otherwise mask stale volume figures.
            try:
                stale_rows = (await db.execute(text("""
                    SELECT symbol
                    FROM market_metadata
                    WHERE symbol = ANY(:syms)
                      AND (
                            volume_24h IS NULL
                         OR volume_24h_updated_at IS NULL
                         OR volume_24h_updated_at < now() - interval '10 minutes'
                      )
                """), {"syms": symbols})).fetchall()
                stale_syms = [r.symbol for r in stale_rows]
                if stale_syms:
                    logger.info("5m: per-symbol Gate ticker fallback for %d stale symbols", len(stale_syms))
                    refreshed = 0
                    for sym in stale_syms:
                        try:
                            ticker = await market_data_service._fetch_gate_ticker(sym)
                            if not ticker:
                                continue
                            price = float(ticker.get("last", 0) or 0)
                            volume = float(ticker.get("quote_volume", 0) or 0)
                            if price <= 0 or volume <= 0:
                                continue
                            await db.execute(text("""
                                UPDATE market_metadata
                                SET price = :price,
                                    volume_24h = :volume,
                                    last_updated = :ts,
                                    volume_24h_updated_at = :ts
                                WHERE symbol = :sym
                            """), {
                                "sym":    sym,
                                "price":  price,
                                "volume": volume,
                                "ts":     datetime.now(timezone.utc),
                            })
                            refreshed += 1
                        except Exception as se:
                            logger.debug("5m: per-symbol fallback failed for %s: %s", sym, se)
                            await db.rollback()
                            continue
                    logger.info("5m: per-symbol Gate ticker fallback refreshed %d symbols", refreshed)
            except Exception as e:
                logger.debug("5m: per-symbol stale-check skipped (non-blocking): %s", e)
                await db.rollback()

            await db.commit()
        except Exception as e:
            logger.error("5m collection failed: %s", e)
            await db.rollback()
            raise

    logger.info(f"5m collection complete: {collected} symbols")
    return collected


@celery_app.task(name="app.tasks.collect_market_data.collect_5m")
def collect_5m():
    count = _run_async(_collect_5m_async())
    # Chain: fresh 5m candles → compute 5m indicators → pipeline scan
    celery_app.send_task("app.tasks.compute_indicators.compute_5m")
    return f"Collected 5m data for {count} symbols"
