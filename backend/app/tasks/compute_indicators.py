"""Celery Task — compute indicators using Feature Engine."""

import asyncio
import json
import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
_VOLUME_LOOKBACK_CANDLES = 20
_STOCHASTIC_WARMUP_OVERLAP = 2


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _derive_min_candles(indicators_config: dict, timeframe: str) -> int:
    ema_periods = indicators_config.get("ema", {}).get("periods", [])
    stochastic = indicators_config.get("stochastic", {})

    required = [
        2,
        indicators_config.get("adx", {}).get("period", 0) * 2,
        indicators_config.get("rsi", {}).get("period", 0) + 1,
        indicators_config.get("macd", {}).get("slow", 0),
        indicators_config.get("atr", {}).get("period", 0),
        indicators_config.get("bollinger", {}).get("period", 0),
        indicators_config.get("zscore", {}).get("lookback", 0),
        max(ema_periods) if ema_periods else 0,
        max(
            # Stochastic uses chained rolling windows (K → smooth → D), so the
            # final warm-up is k + smooth + d minus the two overlapped candles
            # shared at the window boundaries.
            stochastic.get("k", 0) + stochastic.get("smooth", 0) + stochastic.get("d", 0) - _STOCHASTIC_WARMUP_OVERLAP,
            0,
        ),
        _VOLUME_LOOKBACK_CANDLES,
        288 if timeframe == "5m" else 24,
    ]
    return max(required)


async def _compute_async():
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..services.feature_engine import FeatureEngine
    from ..services.seed_service import DEFAULT_INDICATORS

    logger.info("Starting indicator computation...")

    indicators_config = DEFAULT_INDICATORS  # System defaults for centralized computation
    engine = FeatureEngine(indicators_config)
    min_candles_1h = _derive_min_candles(indicators_config, "1h")
    query_limit_1h = max(200, min_candles_1h)
    computed = 0

    async with AsyncSessionLocal() as db:
        # Get all symbols with recent OHLCV data
        symbols_result = await db.execute(text("""
            SELECT DISTINCT symbol FROM ohlcv
            WHERE time > now() - interval '7 days'
        """))
        symbols = [row.symbol for row in symbols_result.fetchall()]

        for symbol in symbols:
            try:
                # Fetch OHLCV data for this symbol
                ohlcv_result = await db.execute(text("""
                    SELECT time, open, high, low, close, volume, quote_volume
                    FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = '1h'
                    ORDER BY time DESC
                    LIMIT :limit
                """), {"symbol": symbol, "limit": query_limit_1h})
                rows = ohlcv_result.fetchall()

                if len(rows) < min_candles_1h:
                    logger.debug(
                        "Skipping 1h indicator computation for %s: only %d candles (need ≥%d)",
                        symbol, len(rows), min_candles_1h,
                    )
                    continue

                df = pd.DataFrame([{
                    "time": r.time, "open": float(r.open), "high": float(r.high),
                    "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                    "quote_volume": float(r.quote_volume) if r.quote_volume is not None else None,
                } for r in reversed(rows)])

                # Calculate indicators
                results = engine.calculate(df)
                if not results:
                    continue

                logger.debug(
                    "Indicator volume audit %s[1h]: last_base=%s last_usdt=%s agg24h_usdt=%s coverage_h=%s candles_24h=%s",
                    symbol,
                    results.get("volume_last_candle_base"),
                    results.get("volume_last_candle_usdt"),
                    results.get("volume_24h_usdt"),
                    results.get("volume_24h_coverage_hours"),
                    results.get("volume_24h_candles"),
                )

                # Store in TimescaleDB
                now = datetime.now(timezone.utc)
                await db.execute(text("""
                    INSERT INTO indicators (time, symbol, timeframe, indicators_json)
                    VALUES (:time, :symbol, :timeframe, :indicators)
                """), {
                    "time": now,
                    "symbol": symbol,
                    "timeframe": "1h",
                    "indicators": json.dumps(results),
                })

                computed += 1

            except Exception as e:
                logger.warning(f"Failed to compute indicators for {symbol}: {e}")
                continue

        await db.commit()

    logger.info(f"Indicator computation complete: {computed} symbols")
    return computed


@celery_app.task(name="app.tasks.compute_indicators.compute")
def compute():
    count = _run_async(_compute_async())
    celery_app.send_task("app.tasks.compute_scores.score")
    return f"Computed indicators for {count} symbols"


async def _compute_5m_async():
    """Compute technical indicators from 5-minute OHLCV candles."""
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..services.feature_engine import FeatureEngine
    from ..services.seed_service import DEFAULT_INDICATORS

    logger.info("Starting 5m indicator computation...")

    indicators_config = DEFAULT_INDICATORS
    engine = FeatureEngine(indicators_config)
    computed = 0
    min_candles_5m = _derive_min_candles(indicators_config, "5m")
    query_limit_5m = max(288, min_candles_5m)

    async with AsyncSessionLocal() as db:
        # Only symbols that have recent 5m candles
        symbols_result = await db.execute(text("""
            SELECT DISTINCT symbol FROM ohlcv
            WHERE timeframe = '5m'
              AND time > now() - interval '2 hours'
        """))
        symbols = [row.symbol for row in symbols_result.fetchall()]

        for symbol in symbols:
            try:
                ohlcv_result = await db.execute(text("""
                    SELECT time, open, high, low, close, volume, quote_volume
                    FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = '5m'
                    ORDER BY time DESC
                    LIMIT :limit
                """), {"symbol": symbol, "limit": query_limit_5m})
                rows = ohlcv_result.fetchall()

                if len(rows) < min_candles_5m:
                    logger.debug(
                        "Skipping 5m indicator computation for %s: only %d candles (need ≥%d)",
                        symbol, len(rows), min_candles_5m,
                    )
                    continue

                df = pd.DataFrame([{
                    "time": r.time, "open": float(r.open), "high": float(r.high),
                    "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                    "quote_volume": float(r.quote_volume) if r.quote_volume is not None else None,
                } for r in reversed(rows)])

                results = engine.calculate(df)
                if not results:
                    continue

                logger.debug(
                    "Indicator volume audit %s[5m]: last_base=%s last_usdt=%s agg24h_usdt=%s coverage_h=%s candles_24h=%s",
                    symbol,
                    results.get("volume_last_candle_base"),
                    results.get("volume_last_candle_usdt"),
                    results.get("volume_24h_usdt"),
                    results.get("volume_24h_coverage_hours"),
                    results.get("volume_24h_candles"),
                )

                now = datetime.now(timezone.utc)
                await db.execute(text("""
                    INSERT INTO indicators (time, symbol, timeframe, indicators_json)
                    VALUES (:time, :symbol, :timeframe, :indicators)
                """), {
                    "time":       now,
                    "symbol":     symbol,
                    "timeframe":  "5m",
                    "indicators": json.dumps(results),
                })

                computed += 1

            except Exception as e:
                logger.warning(f"Failed to compute 5m indicators for {symbol}: {e}")
                continue

        await db.commit()

    logger.info(f"5m indicator computation complete: {computed} symbols")
    return computed


@celery_app.task(name="app.tasks.compute_indicators.compute_5m")
def compute_5m():
    count = _run_async(_compute_5m_async())
    # Chain: fresh 5m indicators → pipeline scan re-evaluates all layers
    celery_app.send_task("app.tasks.pipeline_scan.scan")
    return f"Computed 5m indicators for {count} symbols"
