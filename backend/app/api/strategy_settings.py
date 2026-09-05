"""Authenticated aggregate API for the Strategies settings module."""

from __future__ import annotations

import json
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.profile import Profile
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


@router.post("/multilayer-shadow/activate")
async def activate_multilayer_shadow(
    payload: Dict[str, Any],
    apply: bool = False,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        raw_ids = payload.get("layer_profile_ids") or {}
        layer_ids = {layer: UUID(str(raw_ids[layer])) for layer in ("L1", "L2")}
        return await strategy_settings_service.activate_multilayer_shadow(
            db,
            user_id,
            layer_profile_ids=layer_ids,
            l3_source_identity=dict(payload.get("l3_source_identity") or {}),
            apply=apply,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/multilayer-shadow/disable")
async def disable_multilayer_shadow(
    apply: bool = False,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await strategy_settings_service.disable_multilayer_shadow(
            db, user_id, apply=apply
        )
    except StrategySettingsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/multilayer-runtime/audit")
async def audit_multilayer_runtime(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    config_row = (await db.execute(text("""
        SELECT config_json, updated_at
          FROM config_profiles
         WHERE user_id = :user_id AND pool_id IS NULL
           AND config_type = 'spot_engine' AND is_active IS TRUE
         ORDER BY updated_at DESC LIMIT 1
    """), {"user_id": str(user_id)})).mappings().one_or_none()
    coverage = (await db.execute(text("""
        SELECT timeframe, scheduler_group, count(DISTINCT symbol) AS symbols,
               max(time) AS latest
          FROM indicators
         WHERE market_type = 'spot'
           AND timeframe IN ('1h', '15m', '5m')
           AND time > now() - interval '3 hours'
         GROUP BY timeframe, scheduler_group
         ORDER BY timeframe, scheduler_group
    """))).mappings().all()
    active_symbols = await db.scalar(text("""
        SELECT count(*) FROM pool_coins
         WHERE is_active IS TRUE AND market_type = 'spot'
    """))
    decisions = (await db.execute(text("""
        SELECT count(*) AS total,
               count(*) FILTER (
                   WHERE metrics ? 'multilayer_decision_context_v2'
               ) AS with_mtf,
               count(*) FILTER (
                   WHERE metrics->'multilayer_decision_context_v2'
                         ->>'observational_decision' = 'PASS'
               ) AS mtf_pass,
               count(*) FILTER (
                   WHERE metrics->'multilayer_decision_context_v2'
                         ->>'observational_decision' = 'WAIT'
               ) AS mtf_wait,
               count(*) FILTER (
                   WHERE metrics->'multilayer_decision_context_v2'
                         ->>'observational_decision' = 'REJECT'
               ) AS mtf_reject
          FROM decisions_log
         WHERE user_id = :user_id
           AND created_at > now() - interval '24 hours'
    """), {"user_id": str(user_id)})).mappings().one()
    contract = (
        (((config_row or {}).get("config_json") or {}).get("scanner") or {})
        .get("multilayer_contract")
    )
    return {
        "runtime": {
            "producer_versions": {
                "1h": "mtf_indicator_producer_v1",
                "15m": "mtf_indicator_producer_v1",
                "5m": "compute_5m_v2 + compute_structural_5m_v2",
            },
            "active_spot_symbols": int(active_symbols or 0),
            "coverage": [dict(row) for row in coverage],
            "decisions_24h": dict(decisions),
        },
        "multilayer_contract": contract,
        "spot_engine_updated_at": (
            config_row["updated_at"].isoformat() if config_row else None
        ),
    }


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


@router.post("/multilayer-contract/materialize")
async def materialize_multilayer_contract(
    apply: bool = False,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    rows = (
        await db.execute(
            select(Profile.id, Profile.name)
            .where(
                Profile.user_id == user_id,
                Profile.is_active.is_(True),
                Profile.name.in_(("L1", "L2")),
            )
            .order_by(Profile.name, Profile.id)
        )
    ).all()
    by_name: dict[str, list[UUID]] = {"L1": [], "L2": []}
    for profile_id, name in rows:
        by_name[name].append(profile_id)
    invalid = {name: ids for name, ids in by_name.items() if len(ids) != 1}
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Exactly one active L1 and one active L2 profile are required",
        )
    try:
        return await strategy_settings_service.materialize_multilayer_contract(
            db,
            user_id,
            layer_profile_ids={"L1": by_name["L1"][0], "L2": by_name["L2"][0]},
            apply=apply,
        )
    except StrategySettingsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
