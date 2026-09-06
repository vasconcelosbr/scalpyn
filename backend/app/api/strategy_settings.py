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
from ..services.mtf_calibration_service import (
    approve_policy as approve_mtf_policy,
    audit_policy_availability,
    get_run as get_mtf_calibration_run,
    run_calibration as run_mtf_calibration,
)
from .config import get_current_user_id


router = APIRouter(prefix="/api/strategy-settings", tags=["Strategy Settings"])


@router.get("/mtf-calibration/policy-proposal")
async def get_mtf_policy_proposal(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    return await audit_policy_availability(db, user_id=user_id)


@router.post("/mtf-calibration/policy/approve")
async def approve_mtf_calibration_policy(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await approve_mtf_policy(db, user_id=user_id, payload=payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/mtf-calibration/runs")
async def execute_mtf_calibration(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await run_mtf_calibration(db, user_id=user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/mtf-calibration/runs/{run_id}")
async def read_mtf_calibration_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await get_mtf_calibration_run(db, user_id=user_id, run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


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
            calibration_run_id=UUID(str(payload["calibration_run_id"])),
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
        WITH active AS (
          SELECT DISTINCT symbol FROM pool_coins
           WHERE is_active IS TRUE AND market_type = 'spot'
        ), requested(timeframe, scheduler_group) AS (
          VALUES ('1h','structural'), ('15m','structural'),
                 ('5m','structural'), ('5m','microstructure')
        )
        SELECT r.timeframe, r.scheduler_group,
               count(latest.time) AS symbols, max(latest.time) AS latest
          FROM requested r CROSS JOIN active a
          LEFT JOIN LATERAL (
            SELECT i.time FROM indicators i
             WHERE i.symbol = a.symbol AND i.market_type = 'spot'
               AND i.timeframe = r.timeframe
               AND i.scheduler_group = r.scheduler_group
             ORDER BY i.time DESC LIMIT 1
          ) latest ON TRUE
         GROUP BY r.timeframe, r.scheduler_group
         ORDER BY r.timeframe, r.scheduler_group
    """))).mappings().all()
    active_symbols = await db.scalar(text("""
        SELECT count(*) FROM pool_coins
         WHERE is_active IS TRUE AND market_type = 'spot'
    """))
    decisions = (await db.execute(text("""
        SELECT count(*) AS total,
               count(*) FILTER (
                   WHERE metrics ? 'multilayer_decision_context_v4'
                      OR metrics ? 'multilayer_decision_context_v3'
                      OR metrics ? 'multilayer_decision_context_v2'
               ) AS with_mtf,
               count(*) FILTER (
                   WHERE COALESCE(metrics->'multilayer_decision_context_v4',
                                  metrics->'multilayer_decision_context_v3',
                                  metrics->'multilayer_decision_context_v2')
                         ?& ARRAY[
                             'l1_snapshot', 'l1_context_hash',
                             'l2_snapshot', 'l2_context_hash',
                             'l3_confirmation', 'verdicts',
                             'observational_decision', 'computed_at'
                         ]
               ) AS complete_mtf,
               count(*) FILTER (
                   WHERE COALESCE(metrics->'multilayer_decision_context_v4',
                                  metrics->'multilayer_decision_context_v3',
                                  metrics->'multilayer_decision_context_v2')
                         ->>'observational_decision' = 'PASS'
               ) AS mtf_pass,
               count(*) FILTER (
                   WHERE COALESCE(metrics->'multilayer_decision_context_v4',
                                  metrics->'multilayer_decision_context_v3',
                                  metrics->'multilayer_decision_context_v2')
                         ->>'observational_decision' = 'WAIT'
               ) AS mtf_wait,
               count(*) FILTER (
                   WHERE COALESCE(metrics->'multilayer_decision_context_v4',
                                  metrics->'multilayer_decision_context_v3',
                                  metrics->'multilayer_decision_context_v2')
                         ->>'observational_decision' = 'REJECT'
               ) AS mtf_reject
          FROM decisions_log
         WHERE user_id = :user_id
           AND created_at > now() - interval '24 hours'
    """), {"user_id": str(user_id)})).mappings().one()
    calibration = (await db.execute(text("""
        SELECT id, status, policy_version, dataset_hash, failure_reason,
               dataset_manifest, started_at, completed_at
          FROM mtf_calibration_runs
         WHERE user_id = CAST(:user_id AS UUID)
         ORDER BY created_at DESC LIMIT 1
    """), {"user_id": str(user_id)})).mappings().one_or_none()
    l2_states = (await db.execute(text("""
        SELECT state, count(*) AS symbols
          FROM mtf_l2_setup_states
         WHERE user_id = CAST(:user_id AS UUID)
         GROUP BY state ORDER BY state
    """), {"user_id": str(user_id)})).mappings().all()
    contract = (
        (((config_row or {}).get("config_json") or {}).get("scanner") or {})
        .get("multilayer_contract")
    )
    statistical_gate = (
        dict(contract.get("statistical_gate") or {})
        if isinstance(contract, dict) else {}
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
            "l2_setup_states": [dict(row) for row in l2_states],
        },
        "latest_calibration": dict(calibration) if calibration else None,
        "multilayer_contract": contract,
        "statistical_gate": statistical_gate or None,
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
