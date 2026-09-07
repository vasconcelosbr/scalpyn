"""Authenticated aggregate API for the Strategies settings module."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
from ..services.profile_runtime_config import canonical_hash
from ..services.mtf_observation_service import build_controlled_v5_replay
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


@router.post("/multilayer-shadow/refresh-validity")
async def refresh_multilayer_shadow_validity(
    payload: Dict[str, Any],
    apply: bool = False,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await strategy_settings_service.refresh_multilayer_validity(
            db,
            user_id,
            window_started_at=datetime.fromisoformat(
                str(payload["window_started_at"]).replace("Z", "+00:00")
            ),
            window_ended_at=datetime.fromisoformat(
                str(payload["window_ended_at"]).replace("Z", "+00:00")
            ),
            apply=apply,
        )
    except (KeyError, TypeError, ValueError, StrategySettingsValidationError) as exc:
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
          SELECT DISTINCT pc.symbol FROM pool_coins pc
          JOIN pools p ON p.id = pc.pool_id
           WHERE p.user_id = CAST(:user_id AS UUID)
             AND p.is_active IS TRUE AND p.market_type = 'spot'
             AND pc.is_active IS TRUE AND pc.market_type = 'spot'
        ), requested(timeframe, scheduler_group) AS (
          VALUES ('1h','structural'), ('15m','structural'),
                 ('5m','structural'), ('5m','microstructure')
        )
        SELECT r.timeframe, r.scheduler_group,
               count(latest.time) AS symbols, max(latest.time) AS latest,
               min(latest.time) AS oldest
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
    """), {"user_id": str(user_id)})).mappings().all()
    active_symbols = await db.scalar(text("""
        SELECT count(DISTINCT pc.symbol) FROM pool_coins pc
        JOIN pools p ON p.id = pc.pool_id
         WHERE p.user_id = CAST(:user_id AS UUID)
           AND p.is_active IS TRUE AND p.market_type = 'spot'
           AND pc.is_active IS TRUE AND pc.market_type = 'spot'
    """), {"user_id": str(user_id)})
    decisions = (await db.execute(text("""
        SELECT count(*) AS total,
               count(*) FILTER (
                   WHERE metrics ? 'multilayer_decision_context_v5'
                      OR metrics ? 'multilayer_decision_context_v4'
                      OR metrics ? 'multilayer_decision_context_v3'
                      OR metrics ? 'multilayer_decision_context_v2'
               ) AS with_mtf,
               count(*) FILTER (
                   WHERE COALESCE(metrics->'multilayer_decision_context_v5',
                                  metrics->'multilayer_decision_context_v4',
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
                   WHERE COALESCE(metrics->'multilayer_decision_context_v5',
                                  metrics->'multilayer_decision_context_v4',
                                  metrics->'multilayer_decision_context_v3',
                                  metrics->'multilayer_decision_context_v2')
                         ->>'observational_decision' = 'PASS'
               ) AS mtf_pass,
               count(*) FILTER (
                   WHERE COALESCE(metrics->'multilayer_decision_context_v5',
                                  metrics->'multilayer_decision_context_v4',
                                  metrics->'multilayer_decision_context_v3',
                                  metrics->'multilayer_decision_context_v2')
                         ->>'observational_decision' = 'WAIT'
               ) AS mtf_wait,
               count(*) FILTER (
                   WHERE COALESCE(metrics->'multilayer_decision_context_v5',
                                  metrics->'multilayer_decision_context_v4',
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
    actual_producers = (await db.execute(text("""
        WITH active AS (
          SELECT DISTINCT pc.symbol FROM pool_coins pc
          JOIN pools p ON p.id = pc.pool_id
           WHERE p.user_id = CAST(:user_id AS UUID)
             AND p.is_active IS TRUE AND p.market_type = 'spot'
             AND pc.is_active IS TRUE AND pc.market_type = 'spot'
        ), requested(timeframe, scheduler_group) AS (
          VALUES ('1h','structural'), ('15m','structural'),
                 ('5m','structural'), ('5m','microstructure')
        ), latest AS (
          SELECT a.symbol, r.timeframe, r.scheduler_group,
                 snapshot.indicators_json
            FROM active a CROSS JOIN requested r
            LEFT JOIN LATERAL (
              SELECT i.indicators_json FROM indicators i
               WHERE i.symbol = a.symbol AND i.market_type = 'spot'
                 AND i.timeframe = r.timeframe
                 AND i.scheduler_group = r.scheduler_group
               ORDER BY i.time DESC LIMIT 1
            ) snapshot ON TRUE
        )
        SELECT timeframe, scheduler_group,
               array_remove(array_agg(DISTINCT entry.value->>'producer_version'), NULL)
                 AS producer_versions
          FROM latest
          LEFT JOIN LATERAL jsonb_each(latest.indicators_json) entry ON TRUE
         GROUP BY timeframe, scheduler_group
         ORDER BY timeframe, scheduler_group
    """), {"user_id": str(user_id)})).mappings().all()
    context_rows = (await db.execute(text("""
        SELECT id, symbol, created_at,
               COALESCE(metrics->'multilayer_decision_context_v5',
                        metrics->'multilayer_decision_context_v4',
                        metrics->'multilayer_decision_context_v3',
                        metrics->'multilayer_decision_context_v2') AS context
          FROM decisions_log
         WHERE user_id = :user_id
           AND created_at > now() - interval '24 hours'
           AND (metrics ? 'multilayer_decision_context_v5'
             OR metrics ? 'multilayer_decision_context_v4'
             OR metrics ? 'multilayer_decision_context_v3'
             OR metrics ? 'multilayer_decision_context_v2')
         ORDER BY created_at DESC
    """), {"user_id": str(user_id)})).mappings().all()

    hash_validation = {"checked": 0, "valid": 0, "invalid": 0}
    v5_hash_validation = {"checked": 0, "valid": 0, "invalid": 0}
    current_v5_hash_validation = {"checked": 0, "valid": 0, "invalid": 0}
    reason_counts: Dict[str, int] = {}
    v5_reason_counts: Dict[str, int] = {}
    current_v5_reason_counts: Dict[str, int] = {}
    verdict_counts: Dict[str, Dict[str, int]] = {
        layer: {} for layer in ("L1", "L2", "L3", "MTF")
    }
    v5_complete = 0
    current_v5_complete = 0
    last_complete_context = None
    current_last_complete_context = None
    decision_feature_valid_from = None
    try:
        if isinstance(contract, dict) and contract.get("decision_feature_valid_from"):
            decision_feature_valid_from = datetime.fromisoformat(
                str(contract["decision_feature_valid_from"]).replace("Z", "+00:00")
            )
            if decision_feature_valid_from.tzinfo is None:
                decision_feature_valid_from = decision_feature_valid_from.replace(
                    tzinfo=timezone.utc
                )
            else:
                decision_feature_valid_from = decision_feature_valid_from.astimezone(
                    timezone.utc
                )
    except (TypeError, ValueError):
        decision_feature_valid_from = None
    for row in context_rows:
        context_value = dict(row["context"] or {})
        version = str(context_value.get("contract_version") or "")
        in_current_v5_window = bool(
            version == "multilayer_decision_context_v5"
            and decision_feature_valid_from is not None
            and row["created_at"] >= decision_feature_valid_from
        )
        complete = all(
            key in context_value for key in (
                "l1_snapshot", "l1_context_hash", "l2_snapshot",
                "l2_context_hash", "l3_confirmation", "verdicts",
                "observational_decision", "computed_at",
            )
        )
        if complete:
            if version == "multilayer_decision_context_v5":
                v5_complete += 1
                if in_current_v5_window:
                    current_v5_complete += 1
            valid_hashes = True
            for payload in (
                context_value,
                context_value.get("l1_snapshot") or {},
                context_value.get("l2_snapshot") or {},
                context_value.get("l3_confirmation") or {},
            ):
                material = dict(payload)
                expected_hash = material.pop("context_hash", None)
                hash_validation["checked"] += 1
                if version == "multilayer_decision_context_v5":
                    v5_hash_validation["checked"] += 1
                    if in_current_v5_window:
                        current_v5_hash_validation["checked"] += 1
                if expected_hash and expected_hash == canonical_hash(material):
                    hash_validation["valid"] += 1
                    if version == "multilayer_decision_context_v5":
                        v5_hash_validation["valid"] += 1
                        if in_current_v5_window:
                            current_v5_hash_validation["valid"] += 1
                else:
                    valid_hashes = False
                    hash_validation["invalid"] += 1
                    if version == "multilayer_decision_context_v5":
                        v5_hash_validation["invalid"] += 1
                        if in_current_v5_window:
                            current_v5_hash_validation["invalid"] += 1
            if last_complete_context is None:
                last_complete_context = {
                    "decision_id": str(row["id"]),
                    "symbol": str(row["symbol"]),
                    "created_at": row["created_at"].isoformat(),
                    "contract_version": version,
                    "observational_decision": context_value.get("observational_decision"),
                    "context_hash": context_value.get("context_hash"),
                    "hashes_valid": valid_hashes,
                }
            if in_current_v5_window and current_last_complete_context is None:
                current_last_complete_context = {
                    "decision_id": str(row["id"]),
                    "symbol": str(row["symbol"]),
                    "created_at": row["created_at"].isoformat(),
                    "contract_version": version,
                    "observational_decision": context_value.get(
                        "observational_decision"
                    ),
                    "context_hash": context_value.get("context_hash"),
                    "hashes_valid": valid_hashes,
                }
        for layer in ("L1", "L2", "L3"):
            snapshot = (
                context_value.get("l1_snapshot") if layer == "L1"
                else context_value.get("l2_snapshot") if layer == "L2"
                else context_value.get("l3_confirmation")
            ) or {}
            verdict = str(snapshot.get("verdict") or "UNAVAILABLE")
            verdict_counts[layer][verdict] = verdict_counts[layer].get(verdict, 0) + 1
            for reason in snapshot.get("reason_codes") or []:
                reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
                if version == "multilayer_decision_context_v5":
                    v5_reason_counts[str(reason)] = v5_reason_counts.get(str(reason), 0) + 1
                    if in_current_v5_window:
                        current_v5_reason_counts[str(reason)] = (
                            current_v5_reason_counts.get(str(reason), 0) + 1
                        )
        mtf_verdict = str(context_value.get("observational_decision") or "WAIT")
        verdict_counts["MTF"][mtf_verdict] = verdict_counts["MTF"].get(mtf_verdict, 0) + 1
        if context_value.get("reason"):
            reason = str(context_value["reason"])
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if version == "multilayer_decision_context_v5":
                v5_reason_counts[reason] = v5_reason_counts.get(reason, 0) + 1
                if in_current_v5_window:
                    current_v5_reason_counts[reason] = (
                        current_v5_reason_counts.get(reason, 0) + 1
                    )
        for reason in context_value.get("reason_codes") or []:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
            if version == "multilayer_decision_context_v5":
                v5_reason_counts[str(reason)] = v5_reason_counts.get(str(reason), 0) + 1
                if in_current_v5_window:
                    current_v5_reason_counts[str(reason)] = (
                        current_v5_reason_counts.get(str(reason), 0) + 1
                    )

    now = datetime.now(timezone.utc)
    coverage_rows = [dict(row) for row in coverage]
    identity_health: Dict[str, Dict[str, Any]] = {}
    timeframe_seconds = {"1h": 3600, "15m": 900, "5m": 300}
    layer_for_identity = {
        ("1h", "structural"): "L1",
        ("15m", "structural"): "L2",
        ("5m", "structural"): "L3",
        ("5m", "microstructure"): "L3",
    }
    for item in coverage_rows:
        timeframe = str(item["timeframe"])
        group = str(item["scheduler_group"])
        layer = layer_for_identity[(timeframe, group)]
        layer_config = ((contract or {}).get("layers") or {}).get(layer) or {}
        margin = (
            (layer_config.get("validity_margin_seconds_by_group") or {}).get(group)
            or layer_config.get("validity_margin_seconds")
        )
        oldest = item.get("oldest")
        latest = item.get("latest")
        fresh = bool(
            oldest and margin is not None
            and now <= oldest + timedelta(
                seconds=timeframe_seconds[timeframe] + int(margin)
            )
        )
        identity_health[f"{timeframe}:{group}"] = {
            "layer": layer,
            "timeframe": timeframe,
            "scheduler_group": group,
            "covered_symbols": int(item.get("symbols") or 0),
            "active_symbols": int(active_symbols or 0),
            "coverage_complete": int(item.get("symbols") or 0) == int(active_symbols or 0),
            "fresh": fresh,
            "latest_source_at": latest.isoformat() if latest else None,
            "oldest_source_at": oldest.isoformat() if oldest else None,
            "latest_age_seconds": (now - latest).total_seconds() if latest else None,
            "oldest_age_seconds": (now - oldest).total_seconds() if oldest else None,
            "validity_margin_seconds": margin,
        }
    layer_health = {
        layer: {
            "status": "HEALTHY" if all(
                item["coverage_complete"] and item["fresh"]
                for item in identity_health.values() if item["layer"] == layer
            ) else "DEGRADED"
        }
        for layer in ("L1", "L2", "L3")
    }
    try:
        controlled_replay = build_controlled_v5_replay(
            contract=contract or {}, now=now
        )
    except (KeyError, TypeError, ValueError) as exc:
        controlled_replay = {
            "synthetic_controlled_replay": True,
            "status": "FAILED",
            "error_code": str(exc),
            "operational_effect": False,
            "executed_at": now.isoformat(),
        }
    technically_functional = bool(
        contract
        and contract.get("enabled") is True
        and contract.get("activation_mode") == "SHADOW"
        and contract.get("operational_effect") is False
        and contract.get("decision_feature_contract_version") == "multilayer_decision_context_v5"
        and all(item["status"] == "HEALTHY" for item in layer_health.values())
        and decision_feature_valid_from is not None
        and controlled_replay.get("status") == "PASS"
        and controlled_replay.get("operational_effect") is False
        and current_v5_hash_validation["invalid"] == 0
        and current_v5_reason_counts.get("L3_TEMPORAL_IDENTITY_UNAVAILABLE", 0) == 0
        and current_v5_reason_counts.get("CONTEXT_EXPIRED", 0) == 0
    )
    statistical_status = (
        "APPROVED" if calibration and calibration.get("status") == "PASSED"
        else "NOT_APPROVED"
    )
    return {
        "runtime": {
            "producer_versions": {
                f"{row['timeframe']}:{row['scheduler_group']}": (
                    row["producer_versions"] or []
                ) for row in actual_producers
            },
            "active_spot_symbols": int(active_symbols or 0),
            "coverage": coverage_rows,
            "identity_health": identity_health,
            "layer_health": layer_health,
            "decisions_24h": dict(decisions),
            "l2_setup_states": [dict(row) for row in l2_states],
            "verdict_counts_24h": verdict_counts,
            "reason_counts_24h": reason_counts,
            "v5_reason_counts_24h": v5_reason_counts,
            "v5_reason_counts_current_contract": current_v5_reason_counts,
            "hash_validation_24h": hash_validation,
            "v5_hash_validation_24h": v5_hash_validation,
            "v5_hash_validation_current_contract": current_v5_hash_validation,
            "last_complete_context": last_complete_context,
            "last_complete_context_current_contract": current_last_complete_context,
            "v5_complete_contexts_24h": v5_complete,
            "v5_complete_contexts_current_contract": current_v5_complete,
            "current_contract_window_started_at": (
                decision_feature_valid_from.isoformat()
                if decision_feature_valid_from else None
            ),
            "current_contract_window_elapsed_seconds": (
                (now - decision_feature_valid_from).total_seconds()
                if decision_feature_valid_from else None
            ),
            "controlled_replay": controlled_replay,
            "technical_status": (
                "SHADOW_FUNCTIONAL" if technically_functional else "SHADOW_DEGRADED"
            ),
            "statistical_status": statistical_status,
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
