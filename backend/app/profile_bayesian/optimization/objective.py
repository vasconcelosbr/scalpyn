"""Multi-objective aggregate with every component retained for audit."""

from __future__ import annotations

from typing import Mapping

from .robustness_penalties import penalties


def robust_score(
    metrics: Mapping[str, float],
    *,
    changed_parameters: int,
    total_trials: int,
    weights: Mapping[str, float],
) -> tuple[float, dict[str, float]]:
    benefits = {
        "expectancy_component": float(metrics["expectancy_oos"])
        * float(weights["expectancy"]),
        "profit_factor_component": float(metrics["profit_factor_oos"])
        * float(weights["profit_factor"]),
        "stability_component": float(metrics["stability_factor"])
        * float(weights["stability"]),
        "diversity_component": float(metrics["diversity_factor"])
        * float(weights["diversity"]),
        "regime_component": float(metrics["regime_consistency"])
        * float(weights["regime_consistency"]),
        "sl_component": -float(metrics["sl_rate"]) * float(weights["sl_rate"]),
    }
    deductions = penalties(
        metrics,
        changed_parameters=changed_parameters,
        total_trials=total_trials,
        weights=weights,
    )
    score = sum(benefits.values()) - sum(deductions.values())
    return score, {**benefits, **deductions, "robust_score": score}
