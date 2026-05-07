"""Celery Task — OHLCV historical data backfill."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run async code in sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _backfill_async(
    symbols: List[str],
    timeframes: List[str],
    days: int = 180,
    max_parallel: int = 3,
) -> Dict[str, Any]:
    """
    Async implementation of OHLCV backfill.

    Args:
        symbols: List of trading pairs (e.g., ["BTC_USDT", "ETH_USDT"])
        timeframes: List of intervals (e.g., ["1h", "5m"])
        days: Number of days to backfill
        max_parallel: Maximum number of symbols to process in parallel

    Returns:
        Dict with results per timeframe
    """
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..services.ohlcv_backfill_service import OHLCVBackfillService

    logger.info(
        f"[OHLCV_BACKFILL] Starting backfill - "
        f"symbols: {len(symbols)}, timeframes: {timeframes}, days: {days}"
    )

    start_time = datetime.now(timezone.utc)
    results = {}

    async with AsyncSessionLocal() as db:
        service = OHLCVBackfillService(
            session=db,
            exchange="gate.io",
            max_concurrent=5,
            rate_limit_delay=0.5,
        )

        for timeframe in timeframes:
            logger.info(f"[OHLCV_BACKFILL] Processing timeframe: {timeframe}")

            try:
                timeframe_results = await service.backfill_multiple_symbols(
                    symbols=symbols,
                    timeframe=timeframe,
                    days=days,
                    max_parallel=max_parallel,
                )

                results[timeframe] = {
                    "completed": len(timeframe_results),
                    "total_fetched": sum(r.get("fetched", 0) for r in timeframe_results),
                    "total_inserted": sum(r.get("inserted", 0) for r in timeframe_results),
                    "total_errors": sum(r.get("errors", 0) for r in timeframe_results),
                    "details": timeframe_results,
                }

                logger.info(
                    f"[OHLCV_BACKFILL] {timeframe} complete - "
                    f"fetched: {results[timeframe]['total_fetched']}, "
                    f"inserted: {results[timeframe]['total_inserted']}, "
                    f"errors: {results[timeframe]['total_errors']}"
                )

            except Exception as e:
                logger.error(f"[OHLCV_BACKFILL] {timeframe} failed: {e}", exc_info=True)
                results[timeframe] = {
                    "error": str(e),
                    "completed": 0,
                }

    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    logger.info(f"[OHLCV_BACKFILL] All timeframes complete - duration: {duration:.2f}s")

    return {
        "results": results,
        "duration_seconds": duration,
        "start_time": start_time.isoformat(),
        "end_time": datetime.now(timezone.utc).isoformat(),
    }


@celery_app.task(
    name="app.tasks.ohlcv_backfill.backfill",
    bind=True,
    max_retries=1,
)
def backfill(
    self,
    symbols: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
    days: int = 180,
    max_parallel: int = 3,
) -> str:
    """
    Celery task for OHLCV backfill.

    Args:
        symbols: List of trading pairs. If None, fetches from universe.
        timeframes: List of intervals. Defaults to ["1h"].
        days: Number of days to backfill (default: 180)
        max_parallel: Maximum parallel symbol processing (default: 3)

    Returns:
        JSON string with results summary
    """
    import json

    try:
        # Default timeframes
        if timeframes is None:
            timeframes = ["1h"]

        # Fetch symbols from universe if not provided
        if symbols is None:
            logger.info("[OHLCV_BACKFILL] Fetching symbols from universe...")
            from ..services.market_data_service import market_data_service

            async def _get_symbols():
                return await market_data_service.get_universe_symbols({
                    "min_volume_24h": 5_000_000,
                    "max_assets": 100,
                })

            symbols = _run_async(_get_symbols())
            logger.info(f"[OHLCV_BACKFILL] Fetched {len(symbols)} symbols from universe")

        if not symbols:
            logger.warning("[OHLCV_BACKFILL] No symbols to backfill")
            return json.dumps({"status": "skipped", "reason": "no_symbols"})

        # Run backfill
        results = _run_async(_backfill_async(symbols, timeframes, days, max_parallel))

        # Log summary
        total_fetched = sum(
            r.get("total_fetched", 0)
            for r in results["results"].values()
            if isinstance(r, dict)
        )
        total_inserted = sum(
            r.get("total_inserted", 0)
            for r in results["results"].values()
            if isinstance(r, dict)
        )
        total_errors = sum(
            r.get("total_errors", 0)
            for r in results["results"].values()
            if isinstance(r, dict)
        )

        logger.info(
            f"[OHLCV_BACKFILL] Task complete - "
            f"symbols: {len(symbols)}, timeframes: {len(timeframes)}, "
            f"fetched: {total_fetched}, inserted: {total_inserted}, errors: {total_errors}"
        )

        return json.dumps({
            "status": "success",
            "symbols_processed": len(symbols),
            "timeframes": timeframes,
            "total_fetched": total_fetched,
            "total_inserted": total_inserted,
            "total_errors": total_errors,
            "duration_seconds": results["duration_seconds"],
        })

    except Exception as e:
        logger.error(f"[OHLCV_BACKFILL] Task failed: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=60)


@celery_app.task(name="app.tasks.ohlcv_backfill.get_status")
def get_status(
    symbols: Optional[List[str]] = None,
    timeframe: str = "1h",
    target_days: int = 180,
) -> str:
    """
    Get backfill status for symbols.

    Args:
        symbols: List of trading pairs. If None, uses universe.
        timeframe: Interval to check
        target_days: Target number of days

    Returns:
        JSON string with status information
    """
    import json

    async def _get_status_async():
        from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
        from ..services.ohlcv_backfill_service import OHLCVBackfillService

        # Fetch symbols if not provided
        if symbols is None:
            from ..services.market_data_service import market_data_service
            syms = await market_data_service.get_universe_symbols({
                "min_volume_24h": 5_000_000,
                "max_assets": 100,
            })
        else:
            syms = symbols

        async with AsyncSessionLocal() as db:
            service = OHLCVBackfillService(session=db)
            return await service.get_backfill_status(syms, timeframe, target_days)

    try:
        status = _run_async(_get_status_async())
        needs_backfill = [
            symbol for symbol, info in status.items()
            if info.get("needs_backfill", False)
        ]

        return json.dumps({
            "status": "success",
            "timeframe": timeframe,
            "symbols_checked": len(status),
            "needs_backfill": len(needs_backfill),
            "symbols_needing_backfill": needs_backfill[:20],  # Limit output
            "details": status,
        }, indent=2, default=str)

    except Exception as e:
        logger.error(f"[OHLCV_BACKFILL] Status check failed: {e}", exc_info=True)
        return json.dumps({"status": "error", "error": str(e)})
