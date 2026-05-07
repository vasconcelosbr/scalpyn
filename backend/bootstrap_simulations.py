#!/usr/bin/env python3
"""Bootstrap script for initial simulation backfill.

This script performs an initial large-scale simulation run to populate
the trade_simulations table with historical data for ML training.

Usage:
    python bootstrap_simulations.py [--limit 5000]
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.database import AsyncSessionLocal
from app.services.simulation_service import SimulationService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def bootstrap(limit: int = 5000):
    """
    Run initial backfill of simulations.

    Args:
        limit: Maximum number of decisions to process
    """
    logger.info("=" * 80)
    logger.info("SIMULATION BOOTSTRAP - Initial Backfill")
    logger.info("=" * 80)
    logger.info("This will process up to %d decisions from decisions_log", limit)
    logger.info("and populate trade_simulations for ML training.")
    logger.info("")

    async with AsyncSessionLocal() as session:
        service = SimulationService(session)

        # Check current state
        stats_before = await service.get_stats()
        before_count = stats_before.get("total", 0)

        logger.info("Current simulations: %d", before_count)
        logger.info("")
        logger.info("Starting backfill...")
        logger.info("-" * 80)

        # Large batch for initial backfill
        # Use skip_existing=False to process all decisions
        result = await service.run_simulation_batch(
            limit=limit,
            skip_existing=False,  # Process all decisions
            exchange="gate",
        )

        logger.info("-" * 80)
        logger.info("Bootstrap complete!")
        logger.info("")
        logger.info("Results:")
        logger.info("  Total decisions:    %d", result.get("total_decisions", 0))
        logger.info("  Processed:          %d", result.get("processed", 0))
        logger.info("  Skipped:            %d", result.get("skipped", 0))
        logger.info("  Simulated:          %d", result.get("simulated", 0))
        logger.info("  Errors:             %d", result.get("errors", 0))
        logger.info("  Records inserted:   %d", result.get("records_inserted", 0))
        logger.info("")

        # Show updated stats
        stats_after = await service.get_stats()
        after_count = stats_after.get("total", 0)
        new_count = after_count - before_count

        logger.info("=" * 80)
        logger.info("FINAL STATISTICS")
        logger.info("=" * 80)
        logger.info("Total simulations:  %d (+%d)", after_count, new_count)
        logger.info("Wins:               %d (%.2f%%)",
                   stats_after.get("wins", 0),
                   stats_after.get("win_rate", 0))
        logger.info("Losses:             %d (%.2f%%)",
                   stats_after.get("losses", 0),
                   stats_after.get("loss_rate", 0))
        logger.info("Timeouts:           %d", stats_after.get("timeouts", 0))
        logger.info("Long trades:        %d", stats_after.get("long_trades", 0))
        logger.info("Short trades:       %d", stats_after.get("short_trades", 0))
        logger.info("Spot trades:        %d", stats_after.get("spot_trades", 0))
        logger.info("Unique symbols:     %d", stats_after.get("unique_symbols", 0))
        logger.info("Avg time to result: %.2fs",
                   stats_after.get("avg_time_to_result_seconds", 0))
        logger.info("=" * 80)
        logger.info("")

        if after_count >= 1000:
            logger.info("✓ Dataset ready for ML training!")
            logger.info("  Run: curl -X POST http://localhost:8000/api/ml/train")
        else:
            logger.info("⚠ Dataset size: %d (recommended minimum: 1000)", after_count)
            logger.info("  Consider running with --limit 5000 for better training data")

        logger.info("")
        logger.info("✓ Automatic simulation now active (every 10 minutes)")
        logger.info("  Monitor: GET /api/simulations/status")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Bootstrap trade simulations for ML training"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum number of decisions to process (default: 5000)"
    )

    args = parser.parse_args()

    try:
        asyncio.run(bootstrap(args.limit))
    except KeyboardInterrupt:
        logger.info("\n\nBootstrap interrupted by user")
        sys.exit(1)
    except Exception as exc:
        logger.error("Bootstrap failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
