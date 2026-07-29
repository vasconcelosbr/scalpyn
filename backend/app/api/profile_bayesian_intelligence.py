"""Authenticated, fail-closed API for Profile Bayesian Intelligence."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.profile import Profile
from ..profile_bayesian.analysis_service import dependency_versions
from ..profile_bayesian.batch_reporting import build_batch_report
from ..profile_bayesian.candidate_adapter import CandidateAdapter
from ..profile_bayesian.config import (
    ANALYSIS_ONLY_TEMPLATE_ID,
    AUTHORITY,
    BayesianPolicy,
    PolicyConfigurationError,
    feature_flags,
    load_analysis_only_policy_template,
    require_analysis_only,
)
from ..profile_bayesian.metrics import (
    ANALYSIS_TOTAL,
    OPTIMIZATION_STUDIES,
    increment,
)
from ..profile_bayesian.optimization.search_space import build_search_space
from ..profile_bayesian.schemas import (
    AnalyzeBatchRequest,
    AnalyzeRequest,
    CreateCandidateRequest,
    OptimizationRequest,
    SubmitCandidateRequest,
)
from ..services.config_service import config_service
from ..tasks.task_dispatch import enqueue
from .config import get_current_user_id


router = APIRouter(
    prefix="/api/profile-intelligence",
    tags=["profile-bayesian-intelligence"],
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, UUID)):
        return str(value)
    if hasattr(value, "as_tuple"):
        return float(value)
    return value


def _stable_key(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


def _policy_summary(policy: BayesianPolicy | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    values = policy.values
    return {
        "policy_version": values.get("policy_version"),
        "mode": values.get("mode"),
        "max_trades": values.get("max_trades"),
        "max_runtime_seconds": values.get("max_runtime_seconds"),
        "diagnostic_gates": {
            "max_rhat": values.get("max_rhat"),
            "min_mcmc_effective_sample_size": values.get(
                "min_mcmc_effective_sample_size"
            ),
            "max_divergences": values.get("max_divergences"),
        },
        "sampler_config": values.get("sampler_config"),
        "split_config": values.get("split_config"),
        "population_config": values.get("population_config"),
        "bayesian_model": values.get("bayesian_model"),
        "permissions": values.get("permissions"),
    }


async def _policy(
    db: AsyncSession,
    user_id: UUID,
    permission: str | None = None,
) -> BayesianPolicy:
    try:
        policy = BayesianPolicy.from_mapping(
            await config_service.get_config(db, "profile_bayesian", user_id)
        )
        if permission:
            policy.require_permission(permission)
        return policy
    except PolicyConfigurationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "PROFILE_BAYESIAN_POLICY_MISSING", "message": str(exc)},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _require_flag(name: str) -> None:
    flags = feature_flags()
    if not flags.enabled or getattr(flags, name) is not True:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FEATURE_FLAG_DISABLED",
                "flag": name,
                "mutation_applied": False,
            },
        )


async def _profile_for_user(
    db: AsyncSession, user_id: UUID, profile_id: UUID
) -> Profile:
    profile = await db.get(Profile, profile_id)
    if not profile or profile.user_id != user_id:
        raise HTTPException(status_code=404, detail="profile_not_found")
    return profile


def _analysis_run_values(
    *,
    run_id: UUID,
    user_id: UUID,
    profile_id: UUID,
    profile_version_id: UUID | None,
    scoped_key: str,
    random_seed: int,
    policy: BayesianPolicy,
    filters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": str(run_id),
        "user_id": str(user_id),
        "profile_id": str(profile_id),
        "profile_version_id": (
            str(profile_version_id) if profile_version_id else None
        ),
        "key": scoped_key,
        "random_seed": random_seed,
        "code_version": str(
            policy.values.get("policy_version", "profile_bayesian_v1")
        ),
        "git_commit": os.getenv("GIT_COMMIT_SHA"),
        "model_config": json.dumps(
            {
                "models": (
                    [
                        "hierarchical_multinomial_outcome",
                        "identified_conditional_net_pnl_student_t",
                    ]
                    if policy.values.get("policy_version") == "analysis_only_v2"
                    else [
                        "hierarchical_tp_logit",
                        "hierarchical_net_pnl_student_t",
                    ]
                ),
                "min_feature_coverage": policy.float("min_feature_coverage"),
                "bayesian_model": policy.values.get("bayesian_model"),
                "split_config": policy.values.get("split_config"),
                "population_config": policy.values.get("population_config"),
            }
        ),
        "sampler_config": json.dumps(policy.values["sampler_config"]),
        "dependency_versions": json.dumps(dependency_versions()),
        "filters": json.dumps(filters),
        "requested_by": str(user_id),
    }


async def _insert_analysis_run(
    db: AsyncSession,
    values: dict[str, Any],
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO profile_bayesian_analysis_runs (
                id, user_id, profile_id, profile_version_id, idempotency_key,
                status, random_seed, code_version, git_commit, model_config,
                sampler_config, dependency_versions, filters, requested_by
            ) VALUES (
                :id, :user_id, :profile_id, :profile_version_id, :key,
                'PENDING', :random_seed, :code_version, :git_commit,
                CAST(:model_config AS JSONB), CAST(:sampler_config AS JSONB),
                CAST(:dependency_versions AS JSONB), CAST(:filters AS JSONB),
                :requested_by
            )
            """
        ),
        values,
    )


async def _enqueue_analysis_run(
    db: AsyncSession,
    run_id: UUID | str,
    *,
    max_runtime_seconds: int,
) -> str:
    task_id = enqueue(
        "app.tasks.profile_bayesian_intelligence.analyze",
        dedup_key=f"profile-bayesian-analysis:{run_id}",
        ttl_seconds=max_runtime_seconds,
        queue="profile_bayesian",
        args=(str(run_id),),
    )
    await db.execute(
        text(
            """
            UPDATE profile_bayesian_analysis_runs
            SET task_id = :task_id, updated_at = now()
            WHERE id = :id
            """
        ),
        {"id": str(run_id), "task_id": task_id},
    )
    await db.commit()
    increment(ANALYSIS_TOTAL)
    return task_id


@router.get("/bayesian/status")
async def module_status(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    raw = await config_service.get_config(db, "profile_bayesian", user_id)
    policy: BayesianPolicy | None = None
    try:
        policy = BayesianPolicy.from_mapping(raw)
        policy_configured = True
        policy_error = None
    except PolicyConfigurationError as exc:
        policy_configured = False
        policy_error = str(exc)
    flags = feature_flags()
    return {
        "flags": flags.__dict__,
        "authority": AUTHORITY.__dict__,
        "policy_configured": policy_configured,
        "policy_error": policy_error,
        "policy": _policy_summary(policy),
        "activation": {
            "template_id": ANALYSIS_ONLY_TEMPLATE_ID,
            "mode": "analysis_only",
            "can_activate": not policy_configured,
            "can_upgrade": (
                policy_configured
                and policy is not None
                and policy.values.get("policy_version")
                != ANALYSIS_ONLY_TEMPLATE_ID
            ),
        },
        "replay": {
            "supported": False,
            "reason": "existing_profile_replay_engine_is_stub",
        },
        "dependencies": dependency_versions(),
    }


@router.get("/bayesian/policy")
async def get_bayesian_policy(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    raw = await config_service.get_config(db, "profile_bayesian", user_id)
    if not raw:
        return {"configured": False, "data": None, "summary": None}
    try:
        policy = BayesianPolicy.from_mapping(raw)
    except PolicyConfigurationError as exc:
        return {
            "configured": False,
            "data": raw,
            "summary": None,
            "error": str(exc),
        }
    return {
        "configured": True,
        "data": dict(policy.values),
        "summary": _policy_summary(policy),
    }


@router.put("/bayesian/policy")
async def update_bayesian_policy(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    try:
        policy = BayesianPolicy.from_mapping(payload)
        require_analysis_only(policy)
    except PolicyConfigurationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"INVALID_ANALYSIS_ONLY_POLICY: {exc}",
        ) from exc
    updated = await config_service.update_config(
        db=db,
        config_type="profile_bayesian",
        user_id=user_id,
        new_json=dict(policy.values),
        changed_by=user_id,
        change_description="Updated analysis-only Bayesian policy via Profile Intelligence UI",
    )
    return {
        "configured": True,
        "updated": True,
        "data": updated,
        "summary": _policy_summary(policy),
    }


@router.post("/bayesian/policy/activate-analysis-only")
async def activate_analysis_only_policy(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    current_raw = await config_service.get_config(db, "profile_bayesian", user_id)
    if current_raw:
        try:
            current = BayesianPolicy.from_mapping(current_raw)
        except PolicyConfigurationError:
            current = None
        if current is not None:
            try:
                require_analysis_only(current)
            except PolicyConfigurationError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "EXISTING_POLICY_REQUIRES_EXPLICIT_REVIEW: "
                        f"{exc}"
                    ),
                ) from exc
            return {
                "configured": True,
                "created": False,
                "summary": _policy_summary(current),
            }

    policy = load_analysis_only_policy_template()
    updated = await config_service.update_config(
        db=db,
        config_type="profile_bayesian",
        user_id=user_id,
        new_json=dict(policy.values),
        changed_by=user_id,
        change_description=(
            "Activated versioned analysis-only Bayesian policy from Profile Intelligence UI"
        ),
    )
    return {
        "configured": True,
        "created": True,
        "data": updated,
        "summary": _policy_summary(policy),
        "safety": {
            "optimization_enabled": False,
            "candidate_creation_enabled": False,
            "shadow_submission_enabled": False,
            "automatic_activation": False,
        },
    }


@router.post("/bayesian/policy/upgrade-analysis-v2")
async def upgrade_analysis_v2_policy(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    current_raw = await config_service.get_config(db, "profile_bayesian", user_id)
    if current_raw:
        try:
            current = BayesianPolicy.from_mapping(current_raw)
            require_analysis_only(current)
        except PolicyConfigurationError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"EXISTING_POLICY_REQUIRES_EXPLICIT_REVIEW: {exc}",
            ) from exc
        if current.values.get("policy_version") == ANALYSIS_ONLY_TEMPLATE_ID:
            return {
                "configured": True,
                "upgraded": False,
                "summary": _policy_summary(current),
            }
    policy = load_analysis_only_policy_template()
    updated = await config_service.update_config(
        db=db,
        config_type="profile_bayesian",
        user_id=user_id,
        new_json=dict(policy.values),
        changed_by=user_id,
        change_description=(
            "Upgraded Bayesian policy to analysis_only_v2 with temporal holdout, "
            "non-centered hierarchy, and coherent net-EV estimand"
        ),
    )
    return {
        "configured": True,
        "upgraded": True,
        "data": updated,
        "summary": _policy_summary(policy),
        "safety": {
            "optimization_enabled": False,
            "candidate_creation_enabled": False,
            "shadow_submission_enabled": False,
            "automatic_activation": False,
        },
    }


@router.post(
    "/{profile_id}/bayesian/analyze",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_analysis(
    profile_id: UUID,
    request: AnalyzeRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_flag("analysis_enabled")
    policy = await _policy(db, user_id, "profile_bayesian.run_analysis")
    await _profile_for_user(db, user_id, profile_id)
    scoped_key = _stable_key(str(user_id), str(profile_id), request.idempotency_key)
    existing = (
        await db.execute(
            text(
                """
                SELECT id, status, task_id
                FROM profile_bayesian_analysis_runs
                WHERE idempotency_key = :key
                """
            ),
            {"key": scoped_key},
        )
    ).mappings().first()
    if existing:
        return {**_jsonable(dict(existing)), "created": False}
    run_id = uuid4()
    filters = {
        "window_from": request.window_from.isoformat(),
        "window_to": request.window_to.isoformat(),
        "policy_key": request.policy_key,
        "indicator_names": request.indicator_names,
    }
    await _insert_analysis_run(
        db,
        _analysis_run_values(
            run_id=run_id,
            user_id=user_id,
            profile_id=profile_id,
            profile_version_id=request.profile_version_id,
            scoped_key=scoped_key,
            random_seed=request.random_seed,
            policy=policy,
            filters=filters,
        ),
    )
    await db.commit()
    task_id = await _enqueue_analysis_run(
        db,
        run_id,
        max_runtime_seconds=policy.int("max_runtime_seconds"),
    )
    return {
        "id": str(run_id),
        "status": "PENDING",
        "task_id": task_id,
        "created": True,
        "flags_remain_unchanged": True,
    }


@router.post(
    "/bayesian/batches",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_analysis_batch(
    request: AnalyzeBatchRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_flag("analysis_enabled")
    policy = await _policy(db, user_id, "profile_bayesian.run_analysis")
    for profile_id in request.profile_ids:
        await _profile_for_user(db, user_id, profile_id)

    batch_request_key = _stable_key(
        str(user_id), "profile_bayesian_batch", request.idempotency_key
    )
    existing = (
        await db.execute(
            text(
                """
                SELECT id, filters->>'batch_id' AS batch_id, task_id
                FROM profile_bayesian_analysis_runs
                WHERE user_id = :user_id
                  AND filters->>'batch_request_key' = :batch_request_key
                ORDER BY (filters->>'batch_position')::integer
                """
            ),
            {
                "user_id": str(user_id),
                "batch_request_key": batch_request_key,
            },
        )
    ).mappings().all()

    created = not bool(existing)
    if existing:
        batch_id = str(existing[0]["batch_id"])
        run_rows = list(existing)
    else:
        batch_id = str(uuid4())
        run_rows = []
        total = len(request.profile_ids)
        for position, profile_id in enumerate(request.profile_ids, start=1):
            run_id = uuid4()
            filters = {
                "window_from": request.window_from.isoformat(),
                "window_to": request.window_to.isoformat(),
                "policy_key": request.policy_key,
                "indicator_names": request.indicator_names,
                "batch_id": batch_id,
                "batch_request_key": batch_request_key,
                "batch_total": total,
                "batch_position": position,
            }
            await _insert_analysis_run(
                db,
                _analysis_run_values(
                    run_id=run_id,
                    user_id=user_id,
                    profile_id=profile_id,
                    profile_version_id=None,
                    scoped_key=_stable_key(
                        str(user_id),
                        str(profile_id),
                        batch_request_key,
                    ),
                    random_seed=request.random_seed,
                    policy=policy,
                    filters=filters,
                ),
            )
            run_rows.append(
                {"id": run_id, "batch_id": batch_id, "task_id": None}
            )
        await db.commit()

    enqueued = 0
    enqueue_failures: list[str] = []
    for row in run_rows:
        if row.get("task_id"):
            enqueued += 1
            continue
        try:
            await _enqueue_analysis_run(
                db,
                row["id"],
                max_runtime_seconds=policy.int("max_runtime_seconds"),
            )
            enqueued += 1
        except Exception:
            await db.rollback()
            enqueue_failures.append(str(row["id"]))

    return {
        "batch_id": batch_id,
        "status": "PENDING",
        "total": len(run_rows),
        "enqueued": enqueued,
        "enqueue_failures": enqueue_failures,
        "created": created,
        "flags_remain_unchanged": True,
    }


@router.get("/bayesian/batches/latest")
async def latest_analysis_batch(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    explicit = (
        await db.execute(
            text(
                """
                SELECT filters->>'batch_id' AS batch_id,
                       MAX(created_at) AS last_created
                FROM profile_bayesian_analysis_runs
                WHERE user_id = :user_id
                  AND COALESCE(filters->>'batch_id', '') <> ''
                GROUP BY filters->>'batch_id'
                ORDER BY last_created DESC
                LIMIT 1
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().first()
    legacy = (
        await db.execute(
            text(
                """
                SELECT random_seed,
                       filters->>'window_from' AS window_from,
                       filters->>'window_to' AS window_to,
                       MIN(created_at) AS first_created,
                       MAX(created_at) AS last_created,
                       COUNT(*) AS run_count
                FROM profile_bayesian_analysis_runs
                WHERE user_id = :user_id
                  AND COALESCE(filters->>'batch_id', '') = ''
                  AND created_at >= now() - interval '14 days'
                GROUP BY random_seed,
                         filters->>'window_from',
                         filters->>'window_to'
                HAVING COUNT(*) > 1
                   AND MAX(created_at) - MIN(created_at) <= interval '10 minutes'
                ORDER BY last_created DESC
                LIMIT 1
                """
            ),
            {"user_id": str(user_id)},
        )
    ).mappings().first()

    use_legacy = bool(
        legacy
        and (
            not explicit
            or legacy["last_created"] > explicit["last_created"]
        )
    )
    if not explicit and not legacy:
        return {"item": None}

    if use_legacy:
        identity = {
            "batch_id": (
                f"legacy:{legacy['random_seed']}:"
                f"{legacy['first_created'].isoformat()}"
            ),
            "legacy_batch": True,
        }
        runs = (
            await db.execute(
                text(
                    """
                    SELECT r.id, r.profile_id, p.name AS profile_name,
                           r.status, r.diagnostic_status, r.random_seed,
                           r.warnings, r.error_message, r.task_id,
                           r.created_at, r.started_at, r.finished_at,
                           d.row_count, d.dataset_hash
                    FROM profile_bayesian_analysis_runs r
                    JOIN profiles p ON p.id = r.profile_id
                    LEFT JOIN profile_bayesian_dataset_snapshots d
                      ON d.id = r.dataset_snapshot_id
                    WHERE r.user_id = :user_id
                      AND r.random_seed = :random_seed
                      AND r.filters->>'window_from' = :window_from
                      AND r.filters->>'window_to' = :window_to
                      AND r.created_at BETWEEN :first_created AND :last_created
                    ORDER BY r.created_at, p.name
                    """
                ),
                {
                    "user_id": str(user_id),
                    "random_seed": legacy["random_seed"],
                    "window_from": legacy["window_from"],
                    "window_to": legacy["window_to"],
                    "first_created": legacy["first_created"],
                    "last_created": legacy["last_created"],
                },
            )
        ).mappings().all()
    else:
        identity = {
            "batch_id": str(explicit["batch_id"]),
            "legacy_batch": False,
        }
        runs = (
            await db.execute(
                text(
                    """
                    SELECT r.id, r.profile_id, p.name AS profile_name,
                           r.status, r.diagnostic_status, r.random_seed,
                           r.warnings, r.error_message, r.task_id,
                           r.created_at, r.started_at, r.finished_at,
                           d.row_count, d.dataset_hash
                    FROM profile_bayesian_analysis_runs r
                    JOIN profiles p ON p.id = r.profile_id
                    LEFT JOIN profile_bayesian_dataset_snapshots d
                      ON d.id = r.dataset_snapshot_id
                    WHERE r.user_id = :user_id
                      AND r.filters->>'batch_id' = :batch_id
                    ORDER BY (r.filters->>'batch_position')::integer, p.name
                    """
                ),
                {
                    "user_id": str(user_id),
                    "batch_id": explicit["batch_id"],
                },
            )
        ).mappings().all()

    eligible_run_ids = [
        str(run["id"])
        for run in runs
        if run.get("diagnostic_status") in {"VALID", "VALID_WITH_WARNINGS"}
    ]
    effects: list[Any] = []
    if eligible_run_ids:
        run_id_array = "{" + ",".join(eligible_run_ids) + "}"
        effects = list(
            (
                await db.execute(
                    text(
                        """
                        SELECT e.profile_id, e.indicator, e.regime,
                               e.effect_direction, e.estimated_tp_lift,
                               e.estimated_pnl_lift,
                               e.probability_positive_effect,
                               e.direct_sample_size, e.shared_sample_size,
                               e.effective_sample_size, e.evidence_grade,
                               e.diagnostic_status
                        FROM profile_bayesian_indicator_effects e
                        WHERE e.analysis_run_id =
                              ANY(CAST(:run_ids AS UUID[]))
                        ORDER BY e.indicator, e.regime, e.profile_id
                        """
                    ),
                    {"run_ids": run_id_array},
                )
            ).mappings().all()
        )

    report = build_batch_report(
        runs,
        effects,
        batch_id=identity["batch_id"],
        legacy_batch=identity["legacy_batch"],
    )
    return {"item": _jsonable(report)}


@router.get("/{profile_id}/bayesian/latest")
async def latest_analysis(
    profile_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _profile_for_user(db, user_id, profile_id)
    row = (
        await db.execute(
            text(
                """
                SELECT r.*, d.dataset_hash, d.row_count, d.window_from,
                       d.window_to, d.manifest
                FROM profile_bayesian_analysis_runs r
                LEFT JOIN profile_bayesian_dataset_snapshots d
                  ON d.id = r.dataset_snapshot_id
                WHERE r.user_id = :user_id AND r.profile_id = :profile_id
                ORDER BY r.created_at DESC
                LIMIT 1
                """
            ),
            {"user_id": str(user_id), "profile_id": str(profile_id)},
        )
    ).mappings().first()
    if not row:
        return {"item": None, "diagnostics": []}

    diagnostics = (
        await db.execute(
            text(
                """
                SELECT id, analysis_run_id, model_name, status, rhat_max,
                       effective_sample_size_min, divergences,
                       posterior_predictive_check, credible_intervals,
                       sampling_warnings, details, created_at
                FROM profile_bayesian_diagnostics
                WHERE analysis_run_id = :run_id
                ORDER BY model_name
                """
            ),
            {"run_id": str(row["id"])},
        )
    ).mappings().all()
    return {
        "item": _jsonable(dict(row)),
        "diagnostics": [_jsonable(dict(item)) for item in diagnostics],
    }


@router.get("/{profile_id}/bayesian/history")
async def analysis_history(
    profile_id: UUID,
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _profile_for_user(db, user_id, profile_id)
    rows = (
        await db.execute(
            text(
                """
                SELECT id, status, diagnostic_status, random_seed, task_id,
                       warnings, error_message, started_at, finished_at, created_at
                FROM profile_bayesian_analysis_runs
                WHERE user_id = :user_id AND profile_id = :profile_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {
                "user_id": str(user_id),
                "profile_id": str(profile_id),
                "limit": limit,
            },
        )
    ).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}


@router.get("/{profile_id}/bayesian/effects")
async def indicator_effects(
    profile_id: UUID,
    analysis_run_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _profile_for_user(db, user_id, profile_id)
    rows = (
        await db.execute(
            text(
                """
                SELECT e.*
                FROM profile_bayesian_indicator_effects e
                JOIN profile_bayesian_analysis_runs r ON r.id = e.analysis_run_id
                WHERE r.user_id = :user_id
                  AND e.profile_id = :profile_id
                  AND (
                      CAST(:run_id AS UUID) IS NULL
                      OR e.analysis_run_id = CAST(:run_id AS UUID)
                  )
                ORDER BY e.created_at DESC, e.evidence_grade DESC, e.indicator
                LIMIT :limit
                """
            ),
            {
                "user_id": str(user_id),
                "profile_id": str(profile_id),
                "run_id": str(analysis_run_id) if analysis_run_id else None,
                "limit": limit,
            },
        )
    ).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}


@router.post(
    "/{profile_id}/optimization/start",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_optimization(
    profile_id: UUID,
    request: OptimizationRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_flag("optimization_enabled")
    policy = await _policy(db, user_id, "profile_bayesian.run_optimization")
    profile = await _profile_for_user(db, user_id, profile_id)
    analysis = (
        await db.execute(
            text(
                """
                SELECT id, diagnostic_status, filters
                FROM profile_bayesian_analysis_runs
                WHERE id = :id AND user_id = :user_id AND profile_id = :profile_id
                """
            ),
            {
                "id": str(request.analysis_run_id),
                "user_id": str(user_id),
                "profile_id": str(profile_id),
            },
        )
    ).mappings().first()
    if not analysis or analysis["diagnostic_status"] not in {
        "VALID",
        "VALID_WITH_WARNINGS",
    }:
        raise HTTPException(409, detail="analysis_not_eligible_for_optimization")
    scoped_key = _stable_key(
        str(user_id), str(profile_id), request.idempotency_key
    )
    existing = (
        await db.execute(
            text(
                "SELECT id, status, task_id FROM profile_optimization_studies "
                "WHERE idempotency_key = :key"
            ),
            {"key": scoped_key},
        )
    ).mappings().first()
    if existing:
        return {**_jsonable(dict(existing)), "created": False}
    study_id = uuid4()
    search_space = build_search_space(
        profile.config or {}, policy.values["authorized_search_space"]
    )
    await db.execute(
        text(
            """
            INSERT INTO profile_optimization_studies (
                id, user_id, profile_id, analysis_run_id, idempotency_key,
                status, sampler, directions, search_space, constraints,
                windows, random_seed
            ) VALUES (
                :id, :user_id, :profile_id, :analysis_run_id, :key,
                'PENDING', 'TPESampler', CAST(:directions AS JSONB),
                CAST(:search_space AS JSONB), CAST(:constraints AS JSONB),
                CAST(:windows AS JSONB), :random_seed
            )
            """
        ),
        {
            "id": str(study_id),
            "user_id": str(user_id),
            "profile_id": str(profile_id),
            "analysis_run_id": str(request.analysis_run_id),
            "key": scoped_key,
            "directions": json.dumps(["maximize_robust_score"]),
            "search_space": json.dumps(search_space),
            "constraints": json.dumps(
                {
                    key: policy.values[key]
                    for key in (
                        "min_trades",
                        "min_symbols",
                        "min_days",
                        "max_symbol_concentration",
                        "max_drawdown",
                        "min_expectancy_oos",
                        "min_profit_factor",
                        "max_is_oos_degradation",
                        "min_regime_samples",
                    )
                }
            ),
            "windows": json.dumps(policy.values["split_config"]),
            "random_seed": request.random_seed,
        },
    )
    await db.commit()
    task_id = enqueue(
        "app.tasks.profile_bayesian_intelligence.optimize",
        dedup_key=f"profile-bayesian-optimization:{study_id}",
        ttl_seconds=policy.int("max_runtime_seconds"),
        queue="profile_optimization",
        args=(str(study_id),),
    )
    await db.execute(
        text(
            "UPDATE profile_optimization_studies SET task_id=:task_id "
            "WHERE id=:id"
        ),
        {"id": str(study_id), "task_id": task_id},
    )
    await db.commit()
    increment(OPTIMIZATION_STUDIES)
    return {
        "id": str(study_id),
        "status": "PENDING",
        "task_id": task_id,
        "created": True,
    }


@router.get("/{profile_id}/optimization/{study_id}")
async def get_optimization(
    profile_id: UUID,
    study_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                SELECT * FROM profile_optimization_studies
                WHERE id=:id AND user_id=:user_id AND profile_id=:profile_id
                """
            ),
            {
                "id": str(study_id),
                "user_id": str(user_id),
                "profile_id": str(profile_id),
            },
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="optimization_study_not_found")
    return {"item": _jsonable(dict(row))}


@router.get("/{profile_id}/optimization")
async def list_optimizations(
    profile_id: UUID,
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _profile_for_user(db, user_id, profile_id)
    rows = (
        await db.execute(
            text(
                """
                SELECT * FROM profile_optimization_studies
                WHERE user_id=:user_id AND profile_id=:profile_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {
                "user_id": str(user_id),
                "profile_id": str(profile_id),
                "limit": limit,
            },
        )
    ).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}


@router.post(
    "/{profile_id}/bayesian/candidates",
    status_code=status.HTTP_201_CREATED,
)
async def create_candidate(
    profile_id: UUID,
    request: CreateCandidateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_flag("candidate_creation_enabled")
    policy = await _policy(db, user_id, "profile_bayesian.create_candidate")
    await _profile_for_user(db, user_id, profile_id)
    try:
        return dict(
            await CandidateAdapter().create_draft(
                db,
                user_id=user_id,
                profile_id=profile_id,
                analysis_run_id=request.analysis_run_id,
                optimization_study_id=request.optimization_study_id,
                base_profile_version_id=request.base_profile_version_id,
                changes=request.changes,
                evidence=request.evidence,
                idempotency_key=_stable_key(
                    str(user_id), str(profile_id), request.idempotency_key
                ),
                policy=policy,
            )
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.get("/{profile_id}/bayesian/candidates")
async def list_candidates(
    profile_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _profile_for_user(db, user_id, profile_id)
    rows = (
        await db.execute(
            text(
                """
                SELECT * FROM profile_bayesian_candidate_links
                WHERE user_id=:user_id AND profile_id=:profile_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {
                "user_id": str(user_id),
                "profile_id": str(profile_id),
                "limit": limit,
            },
        )
    ).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}


@router.post("/bayesian/candidates/{candidate_id}/submit-replay")
async def submit_replay(
    candidate_id: UUID,
    request: SubmitCandidateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_flag("enabled")
    policy = await _policy(db, user_id, "profile_bayesian.submit_replay")
    try:
        return dict(
            await CandidateAdapter().submit_replay(
                db,
                user_id=user_id,
                candidate_id=candidate_id,
                expected_status=request.expected_status,
                policy=policy,
            )
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.post("/bayesian/candidates/{candidate_id}/submit-shadow")
async def submit_shadow(
    candidate_id: UUID,
    request: SubmitCandidateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_flag("shadow_submission_enabled")
    policy = await _policy(db, user_id, "profile_bayesian.submit_shadow")
    try:
        return dict(
            await CandidateAdapter().submit_shadow(
                db,
                user_id=user_id,
                candidate_id=candidate_id,
                expected_status=request.expected_status,
                policy=policy,
            )
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.get("/{profile_id}/bayesian/audit")
async def audit_history(
    profile_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> dict[str, Any]:
    await _profile_for_user(db, user_id, profile_id)
    rows = (
        await db.execute(
            text(
                """
                SELECT * FROM profile_bayesian_audit_events
                WHERE user_id=:user_id AND profile_id=:profile_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {
                "user_id": str(user_id),
                "profile_id": str(profile_id),
                "limit": limit,
            },
        )
    ).mappings().all()
    return {"items": [_jsonable(dict(row)) for row in rows]}
