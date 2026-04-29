#!/usr/bin/env python3
"""CLI tool for running trade simulations."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.database import AsyncSessionLocal
from app.services.simulation_service import SimulationService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def run_simulation(
    limit: int,
    skip_existing: bool,
    user_id: str | None,
    exchange: str,
):
    """Run simulation batch."""
    logger.info("Starting simulation batch...")
    logger.info("Limit: %d", limit)
    logger.info("Skip existing: %s", skip_existing)
    logger.info("Exchange: %s", exchange)

    async with AsyncSessionLocal() as session:
        service = SimulationService(session)

        # Run batch
        result = await service.run_simulation_batch(
            limit=limit,
            skip_existing=skip_existing,
            user_id=user_id,
            exchange=exchange,
        )

        # Display results
        logger.info("=" * 60)
        logger.info("SIMULATION BATCH COMPLETE")
        logger.info("=" * 60)
        logger.info("Total decisions:    %d", result["total_decisions"])
        logger.info("Processed:          %d", result["processed"])
        logger.info("Skipped:            %d", result["skipped"])
        logger.info("Simulated:          %d", result["simulated"])
        logger.info("Errors:             %d", result["errors"])
        logger.info("Records inserted:   %d", result["records_inserted"])
        logger.info("=" * 60)

        # Get stats
        stats = await service.get_stats()
        if stats:
            logger.info("")
            logger.info("OVERALL STATISTICS")
            logger.info("=" * 60)
            logger.info("Total simulations:  %d", stats["total"])
            logger.info("Wins:               %d (%.2f%%)", stats["wins"], stats["win_rate"])
            logger.info("Losses:             %d (%.2f%%)", stats["losses"], stats["loss_rate"])
            logger.info("Timeouts:           %d", stats["timeouts"])
            logger.info("Long trades:        %d", stats["long_trades"])
            logger.info("Short trades:       %d", stats["short_trades"])
            logger.info("Spot trades:        %d", stats["spot_trades"])
            logger.info("Avg time to result: %.2fs", stats["avg_time_to_result_seconds"])
            logger.info("Unique symbols:     %d", stats["unique_symbols"])
            logger.info("=" * 60)


async def show_stats():
    """Show simulation statistics."""
    async with AsyncSessionLocal() as session:
        service = SimulationService(session)
        stats = await service.get_stats()

        if not stats or stats.get("total", 0) == 0:
            logger.info("No simulation data found.")
            return

        logger.info("=" * 60)
        logger.info("SIMULATION STATISTICS")
        logger.info("=" * 60)
        logger.info("Total simulations:  %d", stats["total"])
        logger.info("Wins:               %d (%.2f%%)", stats["wins"], stats["win_rate"])
        logger.info("Losses:             %d (%.2f%%)", stats["losses"], stats["loss_rate"])
        logger.info("Timeouts:           %d", stats["timeouts"])
        logger.info("Long trades:        %d", stats["long_trades"])
        logger.info("Short trades:       %d", stats["short_trades"])
        logger.info("Spot trades:        %d", stats["spot_trades"])
        logger.info("Avg time to result: %.2fs", stats["avg_time_to_result_seconds"])
        logger.info("Unique symbols:     %d", stats["unique_symbols"])
        logger.info("=" * 60)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Run trade simulations for ML dataset generation"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run simulation batch")
    run_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of decisions to process (default: 100)"
    )
    run_parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Process all decisions, even those already simulated"
    )
    run_parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="User ID to filter decisions (optional)"
    )
    run_parser.add_argument(
        "--exchange",
        type=str,
        default="gate",
        help="Exchange name (default: gate)"
    )

    # Stats command
    subparsers.add_parser("stats", help="Show simulation statistics")

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(run_simulation(
            limit=args.limit,
            skip_existing=not args.no_skip_existing,
            user_id=args.user_id,
            exchange=args.exchange,
        ))
    elif args.command == "stats":
        asyncio.run(show_stats())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
