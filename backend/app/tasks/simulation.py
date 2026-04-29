"""Celery tasks for trade simulations."""

import logging
from typing import Optional

from .celery_app import celery_app
from ..database import CeleryAsyncSessionLocal
from ..services.simulation_service import SimulationService

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.simulation.run_trade_simulation")
def run_trade_simulation(
    decision_id: int,
    user_id: Optional[str] = None,
    exchange: str = "gate",
):
    """
    Run simulation for a single decision.

    Args:
        decision_id: ID of the decision to simulate
        user_id: Optional user ID for config lookup
        exchange: Exchange name
    """
    import asyncio
    from sqlalchemy import select
    from ..models.backoffice import DecisionLog

    async def _run():
        async with CeleryAsyncSessionLocal() as session:
            # Fetch decision
            result = await session.execute(
                select(DecisionLog).where(DecisionLog.id == decision_id)
            )
            decision = result.scalars().first()

            if not decision:
                logger.error("Decision %s not found", decision_id)
                return {
                    "status": "error",
                    "message": f"Decision {decision_id} not found"
                }

            # Get config
            service = SimulationService(session)
            config = await service.get_simulation_config(user_id)

            # Run simulation
            records = await service.simulate_decision(decision, config, exchange)

            if not records:
                logger.warning("No simulation records generated for decision %s", decision_id)
                return {
                    "status": "skipped",
                    "decision_id": decision_id,
                    "message": "No valid simulation records"
                }

            # Insert records
            inserted = await service.repository.bulk_insert_simulations(records)

            logger.info(
                "Simulated decision %s: %d records inserted",
                decision_id, inserted
            )

            return {
                "status": "success",
                "decision_id": decision_id,
                "records_inserted": inserted,
            }

    return asyncio.run(_run())


@celery_app.task(
    name="app.tasks.simulation.run_simulation_batch",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    time_limit=600,  # 10 minutes max
    soft_time_limit=540,  # 9 minutes soft limit
)
def run_simulation_batch(
    self,
    limit: int = 100,
    skip_existing: bool = True,
    user_id: Optional[str] = None,
    exchange: str = "gate",
):
    """
    Run simulation on a batch of decisions with retry logic and timeout protection.

    Args:
        limit: Maximum number of decisions to process (max 1000)
        skip_existing: Skip decisions that already have simulations
        user_id: Optional user ID filter
        exchange: Exchange name

    Returns:
        Summary statistics
    """
    import asyncio
    from datetime import datetime, timezone

    # Enforce maximum batch size
    limit = min(limit, 1000)

    start_time = datetime.now(timezone.utc)
    logger.info(
        "[Simulation] Starting batch simulation: limit=%d, skip_existing=%s, exchange=%s",
        limit, skip_existing, exchange
    )

    async def _run():
        async with CeleryAsyncSessionLocal() as session:
            try:
                service = SimulationService(session)
                result = await service.run_simulation_batch(
                    limit=limit,
                    skip_existing=skip_existing,
                    user_id=user_id,
                    exchange=exchange,
                )

                # Enhanced logging
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                logger.info(
                    "[Simulation] Batch complete in %.2fs: processed=%d, skipped=%d, "
                    "simulated=%d, errors=%d, records_inserted=%d",
                    duration,
                    result.get("processed", 0),
                    result.get("skipped", 0),
                    result.get("simulated", 0),
                    result.get("errors", 0),
                    result.get("records_inserted", 0),
                )

                # Store last run info in result for status endpoint
                result["last_run"] = start_time.isoformat()
                result["duration_seconds"] = duration

                return result

            except Exception as exc:
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                logger.error(
                    "[Simulation] Batch failed after %.2fs: %s",
                    duration, exc, exc_info=True
                )

                # Retry on transient errors
                if self.request.retries < self.max_retries:
                    logger.info(
                        "[Simulation] Retrying batch (attempt %d/%d)",
                        self.request.retries + 1, self.max_retries
                    )
                    raise self.retry(exc=exc)

                raise

    try:
        return asyncio.run(_run())
    except asyncio.TimeoutError:
        logger.error("[Simulation] Task timeout after soft_time_limit")
        raise


@celery_app.task(name="app.tasks.simulation.get_simulation_stats")
def get_simulation_stats():
    """
    Get simulation statistics.

    Returns:
        Dictionary with simulation statistics
    """
    import asyncio

    async def _run():
        async with CeleryAsyncSessionLocal() as session:
            service = SimulationService(session)
            stats = await service.get_stats()
            return stats

    return asyncio.run(_run())
