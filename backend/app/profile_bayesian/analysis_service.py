"""End-to-end offline analysis orchestration for the dedicated worker."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import importlib.metadata
import json
import logging
import os
import time
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..services.config_service import config_service
from .audit import record_event
from .config import BayesianPolicy, feature_flags
from .dataset_builder import BayesianDatasetBuilder
from .diagnostics import DiagnosticResult, analyze_diagnostics
from .evidence_grading import grade_evidence
from .hierarchical_model import HierarchicalModel, prepare_matrix
from .metrics import (
    ANALYSIS_DURATION,
    ANALYSIS_FAILED,
    ANALYSIS_SUCCESS,
    DIVERGENCES,
    NON_CONVERGED,
    SAMPLING_DURATION,
    increment,
)
from .posterior_analyzer import indicator_posteriors
from .schemas import DiagnosticStatus

logger = logging.getLogger(__name__)


def dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in ("pymc", "arviz", "optuna", "numpy", "pandas", "scipy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


async def _set_run_status(
    db: AsyncSession,
    run_id: UUID,
    status: str,
    *,
    diagnostic_status: str | None = None,
    warnings: list[str] | None = None,
    error_message: str | None = None,
    finished: bool = False,
) -> None:
    await db.execute(
        text(
            """
            UPDATE profile_bayesian_analysis_runs
            SET status = :status,
                diagnostic_status = COALESCE(:diagnostic_status, diagnostic_status),
                warnings = COALESCE(CAST(:warnings AS JSONB), warnings),
                error_message = :error_message,
                started_at = CASE
                    WHEN :status = 'BUILDING_DATASET' THEN COALESCE(started_at, now())
                    ELSE started_at
                END,
                finished_at = CASE WHEN :finished THEN now() ELSE finished_at END,
                updated_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": str(run_id),
            "status": status,
            "diagnostic_status": diagnostic_status,
            "warnings": json.dumps(warnings) if warnings is not None else None,
            "error_message": error_message,
            "finished": finished,
        },
    )
    await db.commit()


def _worst_status(*results: DiagnosticResult) -> DiagnosticStatus:
    order = {
        DiagnosticStatus.VALID: 0,
        DiagnosticStatus.VALID_WITH_WARNINGS: 1,
        DiagnosticStatus.INSUFFICIENT_EVIDENCE: 2,
        DiagnosticStatus.NOT_CONVERGED: 3,
        DiagnosticStatus.FAILED: 4,
    }
    return max((result.status for result in results), key=order.__getitem__)


async def execute_analysis(db: AsyncSession, run_id: UUID) -> dict[str, Any]:
    started = time.perf_counter()
    row = (
        await db.execute(
            text(
                """
                SELECT * FROM profile_bayesian_analysis_runs
                WHERE id = :id
                FOR UPDATE
                """
            ),
            {"id": str(run_id)},
        )
    ).mappings().first()
    if not row:
        raise ValueError("analysis run not found")
    if row["status"] not in {"PENDING", "FAILED"}:
        return {"run_id": str(run_id), "status": row["status"], "idempotent": True}
    lock_acquired = await db.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"profile-bayesian-analysis:{run_id}"},
    )
    if lock_acquired is not True:
        return {"run_id": str(run_id), "status": "LOCKED", "idempotent": True}
    flags = feature_flags()
    if not flags.enabled or not flags.analysis_enabled:
        await _set_run_status(
            db,
            run_id,
            "FAILED",
            error_message="PROFILE_BAYESIAN_ANALYSIS_ENABLED is false",
            finished=True,
        )
        return {"run_id": str(run_id), "status": "FAILED"}

    user_id = row["user_id"]
    profile_id = row["profile_id"]
    try:
        raw_policy = await config_service.get_config(db, "profile_bayesian", user_id)
        policy = BayesianPolicy.from_mapping(raw_policy)
        policy.require_permission("profile_bayesian.run_analysis")
        filters = row["filters"] or {}
        await _set_run_status(db, run_id, "BUILDING_DATASET")
        dataset = await BayesianDatasetBuilder().build(
            db,
            user_id=user_id,
            profile_id=profile_id,
            profile_version_id=row["profile_version_id"],
            window_from=datetime.fromisoformat(filters["window_from"]),
            window_to=datetime.fromisoformat(filters["window_to"]),
            max_trades=policy.int("max_trades"),
            requested_policy_key=filters.get("policy_key"),
            requested_indicators=filters.get("indicator_names"),
        )
        snapshot_id = (
            await db.execute(
                text(
                    """
                    INSERT INTO profile_bayesian_dataset_snapshots (
                        id, user_id, profile_id, profile_version_id, dataset_hash,
                        policy_hash, window_from, window_to, row_count,
                        observation_ids, manifest
                    ) VALUES (
                        :id, :user_id, :profile_id, :profile_version_id, :dataset_hash,
                        :policy_hash, :window_from, :window_to, :row_count,
                        CAST(:observation_ids AS JSONB), CAST(:manifest AS JSONB)
                    )
                    ON CONFLICT (user_id, dataset_hash)
                    DO UPDATE SET dataset_hash = EXCLUDED.dataset_hash
                    RETURNING id
                    """
                ),
                {
                    "id": str(uuid4()),
                    "user_id": str(user_id),
                    "profile_id": str(profile_id),
                    "profile_version_id": (
                        str(row["profile_version_id"])
                        if row["profile_version_id"]
                        else None
                    ),
                    "dataset_hash": dataset.dataset_hash,
                    "policy_hash": dataset.policy_hash,
                    "window_from": dataset.window_from,
                    "window_to": dataset.window_to,
                    "row_count": len(dataset.observations),
                    "observation_ids": json.dumps(
                        [item.observation_id for item in dataset.observations]
                    ),
                    "manifest": json.dumps(dataset.manifest, default=str),
                },
            )
        ).scalar_one()
        await db.execute(
            text(
                """
                UPDATE profile_bayesian_analysis_runs
                SET dataset_snapshot_id = :snapshot_id,
                    dependency_versions = CAST(:versions AS JSONB),
                    updated_at = now()
                WHERE id = :run_id
                """
            ),
            {
                "run_id": str(run_id),
                "snapshot_id": str(snapshot_id),
                "versions": json.dumps(dependency_versions()),
            },
        )
        await record_event(
            db,
            user_id=user_id,
            profile_id=profile_id,
            analysis_run_id=run_id,
            event_type="DATASET_SNAPSHOT_CREATED",
            previous_status="BUILDING_DATASET",
            new_status="VALIDATING_DATA",
            payload={
                "dataset_hash": dataset.dataset_hash,
                "row_count": len(dataset.observations),
                "policy_hash": dataset.policy_hash,
            },
        )
        await db.commit()
        if len(dataset.observations) < policy.int("min_direct_samples"):
            warnings = ["direct_sample_size_below_policy"]
            await _persist_preflight_diagnostic(
                db,
                run_id,
                DiagnosticStatus.INSUFFICIENT_EVIDENCE,
                warnings,
            )
            await _set_run_status(
                db,
                run_id,
                "COMPLETED_WITH_WARNINGS",
                diagnostic_status=DiagnosticStatus.INSUFFICIENT_EVIDENCE,
                warnings=warnings,
                finished=True,
            )
            return {
                "run_id": str(run_id),
                "status": "COMPLETED_WITH_WARNINGS",
                "diagnostic_status": "INSUFFICIENT_EVIDENCE",
            }

        await _set_run_status(db, run_id, "SAMPLING")
        matrix = prepare_matrix(
            dataset.observations,
            min_coverage=policy.float("min_feature_coverage"),
        )
        sampler = {
            **dict(policy.values["sampler_config"]),
            "draws": min(
                int(policy.values["sampler_config"]["draws"]), policy.int("max_draws")
            ),
            "tune": min(
                int(policy.values["sampler_config"]["tune"]), policy.int("max_tune")
            ),
            "cores": min(
                int(policy.values["sampler_config"]["cores"]), policy.int("max_workers")
            ),
            "random_seed": int(row["random_seed"]),
        }
        model = HierarchicalModel()
        sample_started = time.perf_counter()
        tp_inference = model.fit_tp(matrix, sampler)
        pnl_inference = model.fit_pnl(matrix, sampler)
        if SAMPLING_DURATION is not None:
            SAMPLING_DURATION.observe(time.perf_counter() - sample_started)
        await _set_run_status(db, run_id, "RUNNING_DIAGNOSTICS")
        diagnostics = [
            (
                "tp_probability",
                analyze_diagnostics(
                    tp_inference,
                    max_rhat=policy.float("max_rhat"),
                    min_effective_sample_size=policy.float(
                        "min_effective_sample_size"
                    ),
                    max_divergences=policy.int("max_divergences"),
                ),
            ),
            (
                "net_pnl",
                analyze_diagnostics(
                    pnl_inference,
                    max_rhat=policy.float("max_rhat"),
                    min_effective_sample_size=policy.float(
                        "min_effective_sample_size"
                    ),
                    max_divergences=policy.int("max_divergences"),
                ),
            ),
        ]
        for name, result in diagnostics:
            await _persist_diagnostic(db, run_id, name, result)
            increment(DIVERGENCES, result.divergences)
            if result.status == DiagnosticStatus.NOT_CONVERGED:
                increment(NON_CONVERGED)
        combined_status = _worst_status(*(item[1] for item in diagnostics))
        warnings = sorted({warning for _, item in diagnostics for warning in item.warnings})
        if combined_status not in {
            DiagnosticStatus.VALID,
            DiagnosticStatus.VALID_WITH_WARNINGS,
        }:
            await _set_run_status(
                db,
                run_id,
                "COMPLETED_WITH_WARNINGS",
                diagnostic_status=combined_status,
                warnings=warnings,
                finished=True,
            )
            return {
                "run_id": str(run_id),
                "status": "COMPLETED_WITH_WARNINGS",
                "diagnostic_status": combined_status,
            }

        await _set_run_status(db, run_id, "ANALYZING_POSTERIOR")
        posterior_effects = indicator_posteriors(
            tp_inference, pnl_inference, matrix.feature_names
        )
        symbol_count = len({item.symbol for item in dataset.observations})
        day_count = len({item.occurred_at.date() for item in dataset.observations})
        regime_count = len(
            {item.regime or "UNKNOWN" for item in dataset.observations}
        )
        effective_n = min(
            item.effective_sample_size_min or 0 for _, item in diagnostics
        )
        for effect in posterior_effects:
            grade = grade_evidence(
                probability_positive=effect["probability_positive_effect"],
                credible_interval=tuple(effect["credible_interval_95"]),
                effective_sample_size=effective_n,
                symbol_count=symbol_count,
                day_count=day_count,
                stable_windows=0,
                consistent_regimes=regime_count,
                diagnostic_status=combined_status,
                policy=policy.values["evidence_grading"],
            )
            recommendation = (
                "CONSIDER_OPTIMIZATION"
                if grade.value in {"STRONG", "VERY_STRONG"}
                else "NO_ACTION"
            )
            await db.execute(
                text(
                    """
                    INSERT INTO profile_bayesian_indicator_effects (
                        id, analysis_run_id, profile_id, indicator, regime,
                        effect_direction, estimated_tp_lift, estimated_pnl_lift,
                        probability_positive_effect, credible_interval_95,
                        direct_sample_size, shared_sample_size, effective_sample_size,
                        evidence_grade, diagnostic_status, recommendation, details
                    ) VALUES (
                        :id, :run_id, :profile_id, :indicator, NULL,
                        :direction, :tp_lift, :pnl_lift, :probability,
                        CAST(:credible_interval AS JSONB), :direct_n, 0, :effective_n,
                        :grade, :diagnostic_status, :recommendation,
                        CAST(:details AS JSONB)
                    )
                    ON CONFLICT (analysis_run_id, indicator, regime) DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "run_id": str(run_id),
                    "profile_id": str(profile_id),
                    "indicator": effect["indicator"],
                    "direction": effect["effect_direction"],
                    "tp_lift": effect["estimated_tp_lift"],
                    "pnl_lift": effect["estimated_pnl_lift"],
                    "probability": effect["probability_positive_effect"],
                    "credible_interval": json.dumps(effect["credible_interval_95"]),
                    "direct_n": len(dataset.observations),
                    "effective_n": effective_n,
                    "grade": grade.value,
                    "diagnostic_status": combined_status.value,
                    "recommendation": recommendation,
                    "details": json.dumps(
                        {
                            "language": "association_not_causation",
                            "symbol_count": symbol_count,
                            "day_count": day_count,
                            "regime_count": regime_count,
                            "feature_coverage": matrix.coverage.get(effect["indicator"]),
                        }
                    ),
                },
            )
        final_status = (
            "COMPLETED"
            if combined_status == DiagnosticStatus.VALID and not warnings
            else "COMPLETED_WITH_WARNINGS"
        )
        await record_event(
            db,
            user_id=user_id,
            profile_id=profile_id,
            analysis_run_id=run_id,
            event_type="ANALYSIS_COMPLETED",
            previous_status="ANALYZING_POSTERIOR",
            new_status=final_status,
            payload={
                "diagnostic_status": combined_status.value,
                "dataset_hash": dataset.dataset_hash,
                "effect_count": len(posterior_effects),
                "recommendation_authority": True,
                "profile_mutation_authority": False,
            },
        )
        await db.commit()
        await _set_run_status(
            db,
            run_id,
            final_status,
            diagnostic_status=combined_status.value,
            warnings=warnings,
            finished=True,
        )
        increment(ANALYSIS_SUCCESS)
        if ANALYSIS_DURATION is not None:
            ANALYSIS_DURATION.observe(time.perf_counter() - started)
        return {
            "run_id": str(run_id),
            "status": final_status,
            "diagnostic_status": combined_status.value,
            "effect_count": len(posterior_effects),
        }
    except Exception as exc:
        logger.exception(
            "profile_bayesian_analysis_failed analysis_run_id=%s profile_id=%s",
            run_id,
            profile_id,
        )
        await db.rollback()
        await _set_run_status(
            db,
            run_id,
            "FAILED",
            diagnostic_status=DiagnosticStatus.FAILED.value,
            error_message=f"{type(exc).__name__}: {exc}"[:2000],
            finished=True,
        )
        increment(ANALYSIS_FAILED)
        if ANALYSIS_DURATION is not None:
            ANALYSIS_DURATION.observe(time.perf_counter() - started)
        return {"run_id": str(run_id), "status": "FAILED", "error": str(exc)}


async def _persist_preflight_diagnostic(
    db: AsyncSession,
    run_id: UUID,
    status: DiagnosticStatus,
    warnings: list[str],
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO profile_bayesian_diagnostics (
                id, analysis_run_id, model_name, status, divergences,
                posterior_predictive_check, credible_intervals,
                sampling_warnings, details
            ) VALUES (
                :id, :run_id, 'preflight', :status, 0,
                '{}'::jsonb, '{}'::jsonb, CAST(:warnings AS JSONB), '{}'::jsonb
            )
            ON CONFLICT (analysis_run_id, model_name) DO NOTHING
            """
        ),
        {
            "id": str(uuid4()),
            "run_id": str(run_id),
            "status": status.value,
            "warnings": json.dumps(warnings),
        },
    )
    await db.commit()


async def _persist_diagnostic(
    db: AsyncSession,
    run_id: UUID,
    model_name: str,
    result: DiagnosticResult,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO profile_bayesian_diagnostics (
                id, analysis_run_id, model_name, status, rhat_max,
                effective_sample_size_min, divergences,
                posterior_predictive_check, credible_intervals,
                sampling_warnings, details
            ) VALUES (
                :id, :run_id, :model_name, :status, :rhat_max, :ess_min,
                :divergences, CAST(:ppc AS JSONB), CAST(:intervals AS JSONB),
                CAST(:warnings AS JSONB), CAST(:details AS JSONB)
            )
            ON CONFLICT (analysis_run_id, model_name)
            DO UPDATE SET
                status = EXCLUDED.status,
                rhat_max = EXCLUDED.rhat_max,
                effective_sample_size_min = EXCLUDED.effective_sample_size_min,
                divergences = EXCLUDED.divergences,
                posterior_predictive_check = EXCLUDED.posterior_predictive_check,
                credible_intervals = EXCLUDED.credible_intervals,
                sampling_warnings = EXCLUDED.sampling_warnings,
                details = EXCLUDED.details
            """
        ),
        {
            "id": str(uuid4()),
            "run_id": str(run_id),
            "model_name": model_name,
            "status": result.status.value,
            "rhat_max": result.rhat_max,
            "ess_min": result.effective_sample_size_min,
            "divergences": result.divergences,
            "ppc": json.dumps(result.posterior_predictive_check, default=str),
            "intervals": json.dumps(result.credible_intervals, default=str),
            "warnings": json.dumps(result.warnings),
            "details": json.dumps(result.details, default=str),
        },
    )
    await db.commit()
