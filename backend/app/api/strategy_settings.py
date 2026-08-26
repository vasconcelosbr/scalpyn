"""Authenticated aggregate API for the Strategies settings module."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas.strategy_settings import (
    StrategySettingsApplyRequest,
    StrategySettingsValidateRequest,
)
from ..services.strategy_settings_service import (
    StrategySettingsConflictError,
    StrategySettingsValidationError,
    strategy_settings_service,
)
from .config import get_current_user_id


router = APIRouter(prefix="/api/strategy-settings", tags=["Strategy Settings"])


@router.get("/config")
async def get_strategy_settings(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await strategy_settings_service.get_config(db, user_id)


@router.get("/export")
async def export_strategy_settings(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    result = await strategy_settings_service.get_config(db, user_id)
    content = json.dumps(result["config"], ensure_ascii=False, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                'attachment; filename="scalpyn-strategy-settings.json"'
            )
        },
    )


@router.post("/import/validate")
async def validate_strategy_settings_import(
    request: StrategySettingsValidateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        result = await strategy_settings_service.validate_import(
            db, user_id, request.payload
        )
        if request.source_hash and request.source_hash != result["source_hash"]:
            raise StrategySettingsConflictError(
                "The imported source_hash is stale; review against the current configuration"
            )
        return result
    except StrategySettingsConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except StrategySettingsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.put("/config")
async def update_strategy_settings(
    request: StrategySettingsApplyRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await strategy_settings_service.apply(
            db,
            user_id,
            payload=request.payload,
            source_hash=request.source_hash,
            change_description=request.change_description,
            source=request.source,
        )
    except StrategySettingsConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except StrategySettingsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
