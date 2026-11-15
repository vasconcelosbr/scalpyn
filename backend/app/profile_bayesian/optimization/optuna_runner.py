"""Lazy Optuna runner for the dedicated optimization worker."""

from __future__ import annotations

import importlib
from typing import Any, Callable, Mapping

from .constraints import constraint_violations
from .objective import robust_score
from .search_space import suggest_parameters


class OptimizationRuntimeUnavailable(RuntimeError):
    pass


def _load_optuna():
    try:
        return importlib.import_module("optuna")
    except Exception as exc:  # pragma: no cover - optional worker runtime
        raise OptimizationRuntimeUnavailable("Optuna is unavailable") from exc


def run_study(
    *,
    dimensions: list[Mapping[str, Any]],
    policy: Mapping[str, Any],
    evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    random_seed: int,
) -> tuple[Any, list[dict[str, Any]]]:
    optuna = _load_optuna()
    audit_trials: list[dict[str, Any]] = []
    sampler = optuna.samplers.TPESampler(seed=random_seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)

    def objective(trial: Any) -> float:
        parameters = suggest_parameters(trial, dimensions)
        metrics = dict(evaluator(parameters))
        violations = constraint_violations(metrics, policy["constraints"])
        score, components = robust_score(
            metrics,
            changed_parameters=len(parameters),
            total_trials=int(policy["max_trials"]),
            weights=policy["objective_weights"],
        )
        is_valid = not violations
        trial.set_user_attr("metrics", {**metrics, **components})
        trial.set_user_attr("constraint_violations", violations)
        trial.set_user_attr("is_valid", is_valid)
        audit_trials.append(
            {
                "number": trial.number,
                "parameters": parameters,
                "metrics": {**metrics, **components},
                "constraint_violations": violations,
                "is_valid": is_valid,
            }
        )
        return score if is_valid else float("-inf")

    study.optimize(
        objective,
        n_trials=int(policy["max_trials"]),
        timeout=int(policy["max_runtime_seconds"]),
        n_jobs=min(int(policy["max_workers"]), 1),
        gc_after_trial=True,
        show_progress_bar=False,
    )
    return study, audit_trials
