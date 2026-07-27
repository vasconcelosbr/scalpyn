"""Overfit and sensitivity summaries."""

from __future__ import annotations

from typing import Mapping


def overfit_metrics(
    in_sample: Mapping[str, float],
    out_of_sample: Mapping[str, float],
    *,
    changed_parameters: int,
    total_trials: int,
) -> dict[str, float | int | None]:
    is_expectancy = in_sample.get("expectancy")
    oos_expectancy = out_of_sample.get("expectancy")
    degradation = (
        float(is_expectancy) - float(oos_expectancy)
        if is_expectancy is not None and oos_expectancy is not None
        else None
    )
    return {
        "is_expectancy": is_expectancy,
        "oos_expectancy": oos_expectancy,
        "is_oos_degradation": degradation,
        "changed_parameters": changed_parameters,
        "total_trials": total_trials,
    }
