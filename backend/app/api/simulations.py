"""Simulation API endpoints."""

import logging
from typing import Optional
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..database import get_db
from ..services.simulation_service import SimulationService
from ..tasks.simulation import run_simulation_batch, run_trade_simulation
from .config import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/simulations", tags=["Simulations"])


@router.post("/run")
async def trigger_simulation_batch(
    limit: int = Query(100, ge=1, le=1000),
    skip_existing: bool = Query(True),
    exchange: str = Query("gate"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Trigger a batch simulation task.

    Args:
        limit: Maximum number of decisions to process
        skip_existing: Skip decisions that already have simulations
        exchange: Exchange name
        db: Database session
        user_id: Current user ID

    Returns:
        Task info
    """
    try:
        # Trigger Celery task
        task = run_simulation_batch.apply_async(
            kwargs={
                "limit": limit,
                "skip_existing": skip_existing,
                "user_id": str(user_id),
                "exchange": exchange,
            }
        )

        return {
            "status": "queued",
            "task_id": task.id,
            "message": f"Simulation task queued for {limit} decisions",
        }
    except Exception as exc:
        logger.error("Failed to trigger simulation batch: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to trigger simulation batch"
        ) from exc


@router.post("/run/{decision_id}")
async def trigger_single_simulation(
    decision_id: int,
    exchange: str = Query("gate"),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Trigger simulation for a single decision.

    Args:
        decision_id: ID of the decision to simulate
        exchange: Exchange name
        db: Database session
        user_id: Current user ID

    Returns:
        Task info
    """
    try:
        # Trigger Celery task
        task = run_trade_simulation.apply_async(
            kwargs={
                "decision_id": decision_id,
                "user_id": str(user_id),
                "exchange": exchange,
            }
        )

        return {
            "status": "queued",
            "task_id": task.id,
            "decision_id": decision_id,
            "message": f"Simulation task queued for decision {decision_id}",
        }
    except Exception as exc:
        logger.error(
            "Failed to trigger simulation for decision %s: %s",
            decision_id, exc, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger simulation for decision {decision_id}"
        ) from exc


@router.get("/stats")
async def get_simulation_stats(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Get simulation statistics.

    Args:
        db: Database session
        user_id: Current user ID

    Returns:
        Statistics dictionary
    """
    try:
        service = SimulationService(db)
        stats = await service.get_stats()
        return stats
    except Exception as exc:
        logger.error("Failed to get simulation stats: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get simulation stats"
        ) from exc


@router.get("/config")
async def get_simulation_config(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Get current simulation configuration.

    Args:
        db: Database session
        user_id: Current user ID

    Returns:
        Configuration dictionary
    """
    try:
        service = SimulationService(db)
        config = await service.get_simulation_config(str(user_id))
        return config
    except Exception as exc:
        logger.error("Failed to get simulation config: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get simulation config"
        ) from exc


@router.get("/status")
async def get_simulation_status(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Get simulation system status and health metrics.

    Returns:
        Status dictionary with total simulations, last run info, and health status
    """
    try:
        service = SimulationService(db)

        # Get basic stats
        stats = await service.get_stats()

        # Get last simulation timestamp
        result = await db.execute(text("""
            SELECT MAX(created_at) as last_simulation,
                   COUNT(*) as total_simulations
            FROM trade_simulations
        """))
        row = result.fetchone()

        last_simulation = row.last_simulation if row else None
        total_simulations = row.total_simulations if row else 0

        # Check if simulations are current (within last 15 minutes)
        now = datetime.now(timezone.utc)
        is_current = False
        lag_minutes = None

        if last_simulation:
            if last_simulation.tzinfo is None:
                last_simulation = last_simulation.replace(tzinfo=timezone.utc)
            lag_seconds = (now - last_simulation).total_seconds()
            lag_minutes = int(lag_seconds / 60)
            is_current = lag_seconds < 900  # 15 minutes

        # Determine overall status
        if total_simulations == 0:
            status = "empty"
        elif is_current:
            status = "healthy"
        elif lag_minutes and lag_minutes < 60:
            status = "warning"
        else:
            status = "stale"

        return {
            "status": status,
            "total_simulations": total_simulations,
            "last_simulation": last_simulation.isoformat() if last_simulation else None,
            "lag_minutes": lag_minutes,
            "is_current": is_current,
            "stats": stats,
            "system": {
                "automatic_execution": True,
                "schedule": "every 10 minutes",
                "batch_size": 200,
            }
        }

    except Exception as exc:
        logger.error("Failed to get simulation status: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get simulation status"
        ) from exc
