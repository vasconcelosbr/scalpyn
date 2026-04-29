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


@celery_app.task(name="app.tasks.simulation.run_simulation_batch")
def run_simulation_batch(
    limit: int = 100,
    skip_existing: bool = True,
    user_id: Optional[str] = None,
    exchange: str = "gate",
):
    """
    Run simulation on a batch of decisions.

    Args:
        limit: Maximum number of decisions to process
        skip_existing: Skip decisions that already have simulations
        user_id: Optional user ID filter
        exchange: Exchange name

    Returns:
        Summary statistics
    """
    import asyncio

    async def _run():
        async with CeleryAsyncSessionLocal() as session:
            service = SimulationService(session)
            result = await service.run_simulation_batch(
                limit=limit,
                skip_existing=skip_existing,
                user_id=user_id,
                exchange=exchange,
            )

            logger.info("Batch simulation complete: %s", result)
            return result

    return asyncio.run(_run())


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
