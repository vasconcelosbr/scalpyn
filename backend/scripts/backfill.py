#!/usr/bin/env python3
"""CLI script for manual OHLCV backfill execution.

Usage:
    python backend/scripts/backfill.py --symbols BTC_USDT ETH_USDT --timeframes 1h 5m --days 180
    python backend/scripts/backfill.py --all --timeframes 1h --days 90
    python backend/scripts/backfill.py --status
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add backend directory to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import CeleryAsyncSessionLocal as AsyncSessionLocal
from app.services.ohlcv_backfill_service import OHLCVBackfillService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def get_universe_symbols(min_volume: float = 5_000_000, max_assets: int = 100):
    """Fetch symbols from universe."""
    from app.services.market_data_service import market_data_service

    return await market_data_service.get_universe_symbols({
        "min_volume_24h": min_volume,
        "max_assets": max_assets,
    })


async def run_backfill(
    symbols: list[str],
    timeframes: list[str],
    days: int,
    max_parallel: int,
):
    """Execute backfill for specified symbols and timeframes."""
    logger.info(f"Starting backfill: {len(symbols)} symbols, {timeframes}, {days} days")

    async with AsyncSessionLocal() as db:
        service = OHLCVBackfillService(
            session=db,
            exchange="gate.io",
            max_concurrent=5,
            rate_limit_delay=0.5,
        )

        results = {}
        for timeframe in timeframes:
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Processing timeframe: {timeframe}")
            logger.info(f"{'=' * 60}\n")

            timeframe_results = await service.backfill_multiple_symbols(
                symbols=symbols,
                timeframe=timeframe,
                days=days,
                max_parallel=max_parallel,
            )

            results[timeframe] = timeframe_results

            # Print summary
            total_fetched = sum(r.get("fetched", 0) for r in timeframe_results)
            total_inserted = sum(r.get("inserted", 0) for r in timeframe_results)
            total_errors = sum(r.get("errors", 0) for r in timeframe_results)

            logger.info(f"\n{timeframe} Summary:")
            logger.info(f"  Symbols processed: {len(timeframe_results)}")
            logger.info(f"  Total fetched: {total_fetched}")
            logger.info(f"  Total inserted: {total_inserted}")
            logger.info(f"  Errors: {total_errors}")

            # Print per-symbol details
            if total_errors > 0:
                logger.info(f"\n  Errors:")
                for r in timeframe_results:
                    if r.get("errors", 0) > 0:
                        logger.info(f"    {r['symbol']}: {r.get('error_message', 'Unknown error')}")

    return results


async def show_status(
    symbols: list[str],
    timeframes: list[str],
    target_days: int,
):
    """Show backfill status for symbols."""
    async with AsyncSessionLocal() as db:
        service = OHLCVBackfillService(session=db, exchange="gate.io")

        for timeframe in timeframes:
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Status for {timeframe}")
            logger.info(f"{'=' * 60}\n")

            status = await service.get_backfill_status(symbols, timeframe, target_days)

            needs_backfill = []
            has_data = []

            for symbol, info in status.items():
                if info.get("needs_backfill", False):
                    needs_backfill.append(symbol)
                else:
                    has_data.append(symbol)

            logger.info(f"Total symbols: {len(status)}")
            logger.info(f"Has sufficient data: {len(has_data)}")
            logger.info(f"Needs backfill: {len(needs_backfill)}")

            if needs_backfill:
                logger.info(f"\nSymbols needing backfill:")
                for symbol in needs_backfill[:20]:
                    info = status[symbol]
                    logger.info(
                        f"  {symbol}: "
                        f"earliest={info.get('earliest', 'None')}, "
                        f"count={info.get('count', 0)}, "
                        f"days={info.get('days_available', 0)}"
                    )
                if len(needs_backfill) > 20:
                    logger.info(f"  ... and {len(needs_backfill) - 20} more")


def main():
    parser = argparse.ArgumentParser(
        description="OHLCV backfill tool for Scalpyn",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backfill specific symbols
  python backfill.py --symbols BTC_USDT ETH_USDT --timeframes 1h --days 180

  # Backfill all universe symbols
  python backfill.py --all --timeframes 1h 5m --days 90

  # Check backfill status
  python backfill.py --status --timeframes 1h

  # Check status for specific symbols
  python backfill.py --status --symbols BTC_USDT ETH_USDT --timeframes 1h 5m
        """,
    )

    parser.add_argument(
        "--symbols",
        nargs="+",
        help="List of symbols to backfill (e.g., BTC_USDT ETH_USDT)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Backfill all symbols from universe (top 100 by volume)",
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["1h"],
        help="Timeframes to backfill (default: 1h)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=180,
        help="Number of days to backfill (default: 180)",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=3,
        help="Maximum number of symbols to process in parallel (default: 3)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show backfill status instead of running backfill",
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=5_000_000,
        help="Minimum 24h volume for universe filter (default: 5,000,000)",
    )
    parser.add_argument(
        "--max-assets",
        type=int,
        default=100,
        help="Maximum number of assets from universe (default: 100)",
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.status and not args.symbols and not args.all:
        parser.error("Either --symbols, --all, or --status must be specified")

    async def async_main():
        try:
            # Get symbols
            if args.all or (args.status and not args.symbols):
                logger.info("Fetching symbols from universe...")
                symbols = await get_universe_symbols(args.min_volume, args.max_assets)
                logger.info(f"Found {len(symbols)} symbols")
            else:
                symbols = args.symbols

            if not symbols:
                logger.error("No symbols to process")
                return 1

            # Execute command
            if args.status:
                await show_status(symbols, args.timeframes, args.days)
            else:
                start_time = datetime.now(timezone.utc)
                await run_backfill(symbols, args.timeframes, args.days, args.max_parallel)
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()

                logger.info(f"\n{'=' * 60}")
                logger.info(f"Backfill complete!")
                logger.info(f"Total duration: {duration:.2f}s")
                logger.info(f"{'=' * 60}")

            return 0

        except KeyboardInterrupt:
            logger.warning("\nBackfill interrupted by user")
            return 130
        except Exception as e:
            logger.error(f"Backfill failed: {e}", exc_info=True)
            return 1

    exit_code = asyncio.run(async_main())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
