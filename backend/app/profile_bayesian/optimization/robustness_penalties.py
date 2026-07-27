"""Explicit robustness penalties; coefficients come from persisted policy."""

from __future__ import annotations

from typing import Mapping


def penalties(
    metrics: Mapping[str, float],
    *,
    changed_parameters: int,
    total_trials: int,
    weights: Mapping[str, float],
) -> dict[str, float]:
    return {
        "drawdown_penalty": max(0.0, float(metrics["max_drawdown"]))
        * float(weights["drawdown"]),
        "concentration_penalty": max(
            0.0, float(metrics["max_symbol_concentration"])
        )
        * float(weights["concentration"]),
        "overfit_penalty": max(0.0, float(metrics["is_oos_degradation"]))
        * float(weights["overfit"]),
        "complexity_penalty": float(changed_parameters) * float(weights["complexity"]),
        "trial_volume_penalty": float(total_trials) * float(weights["trial_volume"]),
    }
