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
from .posterior_analyzer import (
    indicator_ev_posteriors,
    indicator_posteriors,
    stable_effect_windows,
)
from .schemas import DiagnosticStatus
from .validation.temporal_split import (
    derive_embargo_seconds,
    purged_temporal_split,
)
from .validation.concentration_checks import concentration_metrics
from .validation.data_quality import stratified_feature_quality
from .validation.power_analysis import minimum_detectable_net_ev

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
            SET status = CAST(:status AS VARCHAR(40)),
                diagnostic_status = COALESCE(:diagnostic_status, diagnostic_status),
                warnings = COALESCE(CAST(:warnings AS JSONB), warnings),
                error_message = :error_message,
                started_at = CASE
                    WHEN CAST(:mark_started AS BOOLEAN)
                    THEN COALESCE(started_at, now())
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
            "mark_started": status == "BUILDING_DATASET",
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


def _normalized_outcome(value: str) -> str:
    if value in {"TP", "TP_HIT"}:
        return "TP_HIT"
    if value in {"SL", "SL_HIT"}:
        return "SL_HIT"
    return "TIMEOUT"


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
        is_v2 = policy.values.get("policy_version") == "analysis_only_v2"
        population_config = policy.values.get("population_config") or {}
        bayesian_model_config = policy.values.get("bayesian_model") or {}
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
            required_sources=population_config.get("required_sources", ()),
            required_lineage_statuses=population_config.get(
                "required_lineage_statuses", ()
            ),
            required_barrier_modes=population_config.get(
                "required_barrier_modes", ()
            ),
            required_barrier_contract_versions=population_config.get(
                "required_barrier_contract_versions", ()
            ),
            minimum_entry_at=(
                datetime.fromisoformat(population_config["minimum_entry_at"])
                if is_v2
                else None
            ),
            require_eligible_for_training=bool(
                population_config.get("require_eligible_for_training", False)
            ),
            atr_bucket_edges_pct=population_config.get(
                "atr_bucket_edges_pct", ()
            ),
            selection_strategy=population_config.get(
                "selection_strategy", "oldest_contiguous"
            ),
        )
        dataset_manifest = dict(dataset.manifest)
        discovery_observations = dataset.observations
        validation_observations: tuple[Any, ...] = ()
        if is_v2:
            split_config = policy.values["split_config"]
            effective_embargo = derive_embargo_seconds(
                dataset.observations,
                minimum_embargo_seconds=int(split_config["embargo_seconds"]),
                max_feature_lookback_seconds=int(
                    split_config["max_feature_lookback_seconds"]
                ),
            )
            temporal_split = purged_temporal_split(
                dataset.observations,
                discovery_fraction=float(split_config["discovery_fraction"]),
                validation_fraction=float(split_config["validation_fraction"]),
                embargo_seconds=effective_embargo,
            )
            discovery_observations = temporal_split.discovery
            validation_observations = temporal_split.validation
            dataset_manifest["temporal_split"] = {
                "method": "timestamp_fraction_with_purge",
                "effective_embargo_seconds": effective_embargo,
                "counts": {
                    "discovery": len(temporal_split.discovery),
                    "validation": len(temporal_split.validation),
                    "final_holdout": len(temporal_split.final_holdout),
                },
                "windows": {
                    name: [window[0].isoformat(), window[1].isoformat()]
                    for name, window in temporal_split.windows.items()
                },
                "final_holdout_used_for_fit": False,
                "final_holdout_used_for_grading": False,
            }
        discovery_concentration = concentration_metrics(
            discovery_observations
        )
        preflight_warnings: list[str] = []
        approved_features: tuple[str, ...] | None = None
        if is_v2:
            discovery_outcomes = Counter(
                _normalized_outcome(item.outcome)
                for item in discovery_observations
            )
            validation_outcomes = Counter(
                _normalized_outcome(item.outcome)
                for item in validation_observations
            )
            required_outcomes = ("SL_HIT", "TIMEOUT", "TP_HIT")
            for outcome in required_outcomes:
                if discovery_outcomes[outcome] < int(
                    population_config["min_outcome_samples_discovery"]
                ):
                    preflight_warnings.append(
                        f"discovery_outcome_below_policy:{outcome}"
                    )
                if validation_outcomes[outcome] < int(
                    population_config["min_outcome_samples_validation"]
                ):
                    preflight_warnings.append(
                        f"validation_outcome_below_policy:{outcome}"
                    )
            power = minimum_detectable_net_ev(
                discovery_observations,
                posterior_probability=float(
                    bayesian_model_config["power_probability"]
                ),
                practical_rope_pct=float(
                    bayesian_model_config["practical_effect_rope_pct"]
                ),
            )
            if power["status"] != "CALCULATED":
                preflight_warnings.append("power_analysis_unavailable")
            elif power["minimum_detectable_net_ev_pct"] > float(
                bayesian_model_config["maximum_plausible_edge_pct"]
            ):
                preflight_warnings.append(
                    "minimum_detectable_effect_above_plausible_edge"
                )
            discovery_quality = stratified_feature_quality(
                discovery_observations,
                atr_bucket_edges_pct=population_config[
                    "atr_bucket_edges_pct"
                ],
                min_global_coverage=policy.float("min_feature_coverage"),
                min_group_samples=int(
                    population_config["min_feature_group_samples"]
                ),
                max_missing_outcome_cramers_v=float(
                    population_config["max_missing_outcome_cramers_v"]
                ),
            )
            validation_quality = stratified_feature_quality(
                validation_observations,
                atr_bucket_edges_pct=population_config[
                    "atr_bucket_edges_pct"
                ],
                min_global_coverage=policy.float("min_feature_coverage"),
                min_group_samples=int(
                    population_config["min_feature_group_samples"]
                ),
                max_missing_outcome_cramers_v=float(
                    population_config["max_missing_outcome_cramers_v"]
                ),
            )
            approved_features = tuple(
                sorted(
                    feature
                    for feature, details in discovery_quality[
                        "features"
                    ].items()
                    if details["candidate_for_model"]
                    and not details["violations"]
                    and feature
                    in validation_quality["features"]
                    and validation_quality["features"][feature][
                        "candidate_for_model"
                    ]
                    and not validation_quality["features"][feature][
                        "violations"
                    ]
                )
            )
            if not approved_features:
                preflight_warnings.append(
                    "no_features_pass_stratified_quality_policy"
                )
            dataset_manifest["preflight"] = {
                "power_analysis": power,
                "maximum_plausible_edge_pct": float(
                    bayesian_model_config["maximum_plausible_edge_pct"]
                ),
                "outcome_counts": {
                    "discovery": dict(discovery_outcomes),
                    "validation": dict(validation_outcomes),
                },
                "concentration": discovery_concentration,
                "feature_quality": {
                    "discovery": discovery_quality,
                    "validation": validation_quality,
                    "approved_for_both_windows": list(approved_features),
                    "excluded_from_model": sorted(
                        (
                            set(discovery_quality["features"])
                            | set(validation_quality["features"])
                        )
                        - set(approved_features)
                    ),
                },
            }
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
                    "manifest": json.dumps(dataset_manifest, default=str),
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
                "discovery_row_count": len(discovery_observations),
                "validation_row_count": len(validation_observations),
            },
        )
        await db.commit()
        if len(discovery_observations) < policy.int("min_direct_samples") or (
            is_v2
            and len(validation_observations)
            < policy.int("min_direct_samples")
        ):
            preflight_warnings.append("direct_sample_size_below_policy")
        if discovery_concentration["n_symbols"] < policy.int("min_symbols"):
            preflight_warnings.append("symbol_count_below_policy")
        if discovery_concentration["n_days"] < policy.int("min_days"):
            preflight_warnings.append("day_count_below_policy")
        if discovery_concentration["max_symbol_concentration"] > policy.float(
            "max_symbol_concentration"
        ):
            preflight_warnings.append("symbol_concentration_above_policy")
        if is_v2 and discovery_concentration[
            "max_day_concentration"
        ] > float(population_config["max_day_concentration"]):
            preflight_warnings.append("day_concentration_above_policy")
        if is_v2 and discovery_concentration["effective_symbols"] < float(
            population_config["min_effective_symbols"]
        ):
            preflight_warnings.append("effective_symbol_count_below_policy")
        if is_v2 and discovery_concentration["effective_days"] < float(
            population_config["min_effective_days"]
        ):
            preflight_warnings.append("effective_day_count_below_policy")
        if preflight_warnings:
            warnings = sorted(preflight_warnings)
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
            discovery_observations,
            min_coverage=policy.float("min_feature_coverage"),
            temporal_block_seconds=(
                int(bayesian_model_config["temporal_block_seconds"])
                if is_v2
                else None
            ),
            allowed_features=approved_features,
        )
        validation_matrix = (
            prepare_matrix(
                validation_observations,
                min_coverage=policy.float("min_feature_coverage"),
                temporal_block_seconds=int(
                    bayesian_model_config["temporal_block_seconds"]
                ),
                allowed_features=approved_features,
            )
            if is_v2
            else None
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
            "prior_predictive_samples": int(
                bayesian_model_config.get("prior_predictive_samples", 100)
            ),
            "prior_predictive_pnl_abs_limit_pct": float(
                bayesian_model_config.get(
                    "prior_predictive_pnl_abs_limit_pct", 5.0
                )
            ),
            "pnl_outcome_intercept_prior_sigma_pct": float(
                bayesian_model_config["pnl_outcome_intercept_prior_sigma_pct"]
            ),
            "pnl_student_t_observation_sigma_pct": float(
                bayesian_model_config["pnl_student_t_observation_sigma_pct"]
            ),
            "pnl_student_t_nu": float(
                bayesian_model_config["pnl_student_t_nu"]
            ),
        }
        model = HierarchicalModel()
        sample_started = time.perf_counter()
        if is_v2:
            outcome_inference = model.fit_outcome(matrix, sampler)
            pnl_inference = model.fit_conditional_pnl(matrix, sampler)
            validation_outcome_inference = model.fit_outcome(
                validation_matrix, sampler
            )
            validation_pnl_inference = model.fit_conditional_pnl(
                validation_matrix, sampler
            )
        else:
            tp_inference = model.fit_tp(matrix, sampler)
            pnl_inference = model.fit_pnl(matrix, sampler)
        if SAMPLING_DURATION is not None:
            SAMPLING_DURATION.observe(time.perf_counter() - sample_started)
        await _set_run_status(db, run_id, "RUNNING_DIAGNOSTICS")
        inference_models = (
            [
                ("outcome_discovery", outcome_inference),
                ("net_pnl_discovery", pnl_inference),
                ("outcome_validation", validation_outcome_inference),
                ("net_pnl_validation", validation_pnl_inference),
            ]
            if is_v2
            else [
                ("tp_probability", tp_inference),
                ("net_pnl", pnl_inference),
            ]
        )
        diagnostics = [
            (
                name,
                analyze_diagnostics(
                    inference,
                    max_rhat=policy.float("max_rhat"),
                    min_effective_sample_size=policy.float(
                        "min_mcmc_effective_sample_size"
                    ),
                    max_divergences=policy.int("max_divergences"),
                    prior_predictive_pnl_abs_limit_pct=(
                        float(
                            bayesian_model_config[
                                "prior_predictive_pnl_abs_limit_pct"
                            ]
                        )
                        if is_v2
                        else None
                    ),
                ),
            )
            for name, inference in inference_models
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
        if is_v2:
            practical_rope = float(
                bayesian_model_config["practical_effect_rope_pct"]
            )
            posterior_effects = indicator_ev_posteriors(
                outcome_inference,
                pnl_inference,
                matrix.feature_names,
                practical_effect_rope_pct=practical_rope,
            )
            validation_effects = indicator_ev_posteriors(
                validation_outcome_inference,
                validation_pnl_inference,
                validation_matrix.feature_names,
                practical_effect_rope_pct=practical_rope,
            )
            stable_windows_by_indicator = stable_effect_windows(
                posterior_effects, validation_effects
            )
            validation_by_indicator = {
                item["indicator"]: item for item in validation_effects
            }
        else:
            posterior_effects = indicator_posteriors(
                tp_inference, pnl_inference, matrix.feature_names
            )
            stable_windows_by_indicator = {
                effect["indicator"]: 0 for effect in posterior_effects
            }
            validation_by_indicator = {}
        symbol_count = len({item.symbol for item in discovery_observations})
        day_count = len(
            {item.occurred_at.date() for item in discovery_observations}
        )
        regime_counts = Counter(
            item.regime or "UNKNOWN" for item in discovery_observations
        )
        regime_count = sum(
            count >= policy.int("min_regime_samples")
            for count in regime_counts.values()
            if count > 0
        )
        effective_n = min(
            item.effective_sample_size_min or 0 for _, item in diagnostics
        )
        for effect in posterior_effects:
            stable_windows = stable_windows_by_indicator.get(
                effect["indicator"], 0
            )
            grade = grade_evidence(
                probability_positive=effect["probability_positive_effect"],
                probability_negative=effect.get(
                    "probability_negative_effect"
                ),
                practical_rope=float(effect.get("rope_pct") or 0.0),
                credible_interval=tuple(effect["credible_interval_95"]),
                effective_sample_size=effective_n,
                symbol_count=symbol_count,
                day_count=day_count,
                stable_windows=stable_windows,
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
                        CAST(:credible_interval AS JSONB), :direct_n, :shared_n,
                        :effective_n,
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
                    "direct_n": len(discovery_observations),
                    "shared_n": len(validation_observations),
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
                            "dropped_or_excluded_features": list(
                                matrix.dropped_features
                            ),
                            "quality_approved_features": (
                                list(approved_features)
                                if approved_features is not None
                                else None
                            ),
                            "matrix_rank": matrix.matrix_rank,
                            "estimand": effect.get(
                                "estimand", "legacy_mixed_indicator_effect"
                            ),
                            "practical_effect_rope_pct": effect.get("rope_pct"),
                            "probability_negative_effect": effect.get(
                                "probability_negative_effect"
                            ),
                            "probability_practically_equivalent": effect.get(
                                "probability_practically_equivalent"
                            ),
                            "stable_windows": stable_windows,
                            "validation_effect": validation_by_indicator.get(
                                effect["indicator"]
                            ),
                            "final_holdout_used": False,
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
