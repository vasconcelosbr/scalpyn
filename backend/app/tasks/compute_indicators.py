"""Celery Task — compute indicators using Feature Engine."""

import asyncio
import json
import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _compute_async():
    from ..database import AsyncSessionLocal
    from ..services.feature_engine import FeatureEngine
    from ..services.seed_service import DEFAULT_INDICATORS

    logger.info("Starting indicator computation...")

    indicators_config = DEFAULT_INDICATORS  # System defaults for centralized computation

    engine = FeatureEngine(indicators_config)
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
                    SELECT time, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = '1h'
                    ORDER BY time ASC
                    LIMIT 200
                """), {"symbol": symbol})
                rows = ohlcv_result.fetchall()

                if len(rows) < 20:
                    continue

                df = pd.DataFrame([{
                    "time": r.time, "open": float(r.open), "high": float(r.high),
                    "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                } for r in rows])

                # Calculate indicators
                results = engine.calculate(df)
                if not results:
                    continue

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
    from ..database import AsyncSessionLocal
    from ..services.feature_engine import FeatureEngine
    from ..services.seed_service import DEFAULT_INDICATORS

    logger.info("Starting 5m indicator computation...")

    engine = FeatureEngine(DEFAULT_INDICATORS)
    computed = 0

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
                    SELECT time, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = '5m'
                    ORDER BY time ASC
                    LIMIT 100
                """), {"symbol": symbol})
                rows = ohlcv_result.fetchall()

                if len(rows) < 14:  # minimum candles for indicator validity
                    continue

                df = pd.DataFrame([{
                    "time": r.time, "open": float(r.open), "high": float(r.high),
                    "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                } for r in rows])

                results = engine.calculate(df)
                if not results:
                    continue

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
