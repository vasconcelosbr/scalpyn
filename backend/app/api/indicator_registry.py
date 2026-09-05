"""Authenticated read-only surface for the R6 indicator registry."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.indicator_registry import IndicatorRegistry
from ..models.profile import Profile
from ..services.indicator_registry_service import audit_indicator_registry
from .config import get_current_user_id


router = APIRouter(prefix="/api/indicator-registry", tags=["Indicator Registry"])


def _row(item: IndicatorRegistry) -> dict:
    return {
        "indicator_id": item.indicator_id,
        "alias_of": item.alias_of,
        "phenomenon": item.phenomenon,
        "owning_layer": item.owning_layer,
        "timeframe": item.timeframe,
        "producer": item.producer,
        "source_family": item.source_family,
        "is_blocking": item.is_blocking,
        "composed_inputs": item.composed_inputs or [],
        "contract_version": item.contract_version,
    }


@router.get("")
async def list_indicator_registry(
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
):
    items = (
        await db.execute(
            select(IndicatorRegistry).order_by(IndicatorRegistry.indicator_id)
        )
    ).scalars().all()
    return {"items": [_row(item) for item in items]}


@router.get("/audit")
async def audit_registry(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    registry = (
        await db.execute(
            select(IndicatorRegistry).order_by(IndicatorRegistry.indicator_id)
        )
    ).scalars().all()
    profiles = (
        await db.execute(
            select(Profile.id, Profile.name, Profile.config)
            .where(Profile.user_id == user_id, Profile.is_active.is_(True))
            .order_by(Profile.id)
        )
    ).mappings().all()
    return audit_indicator_registry(
        [_row(item) for item in registry],
        [dict(item) for item in profiles],
    )
