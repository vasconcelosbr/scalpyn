"""Pure chronological walk-forward selection primitives for MTF profiles."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Sequence


class MTFCalibrationConfigRequired(ValueError):
    code = "CONFIG_REQUIRED"


@dataclass(frozen=True)
class FoldResult:
    net_expectancy: float
    max_drawdown: float
    samples: int


def require_calibration_config(config: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "min_samples",
        "fold_count",
        "train_window_rows",
        "test_window_rows",
        "candidate_quantiles",
        "cost_field",
        "return_field",
    )
    missing = [key for key in required if config.get(key) is None]
    if missing:
        raise MTFCalibrationConfigRequired(
            "CONFIG_REQUIRED:" + ",".join(missing)
        )
    parsed = dict(config)
    if int(parsed["min_samples"]) <= 0:
        raise MTFCalibrationConfigRequired("CONFIG_REQUIRED:min_samples")
    return parsed


def chronological_folds(
    rows: Sequence[Mapping[str, Any]], *, train_size: int, test_size: int,
    fold_count: int,
) -> list[tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]]:
    ordered = sorted(rows, key=lambda row: row["decision_at"])
    folds = []
    for fold in range(fold_count):
        test_end = len(ordered) - (fold_count - fold - 1) * test_size
        test_start = test_end - test_size
        train_start = max(0, test_start - train_size)
        if train_start >= test_start or test_start < 0:
            continue
        folds.append((ordered[train_start:test_start], ordered[test_start:test_end]))
    return folds


def max_drawdown(returns: Iterable[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in returns:
        equity += float(value)
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return abs(worst)


def select_candidate(
    candidate_folds: Mapping[str, Sequence[FoldResult]],
    *, baseline_folds: Sequence[FoldResult],
) -> str | None:
    """Select by OOS median, then worst DD, dispersion, and identifier."""
    baseline_expectancy = median(row.net_expectancy for row in baseline_folds)
    baseline_worst_dd = max(row.max_drawdown for row in baseline_folds)
    eligible = []
    for identifier, folds in candidate_folds.items():
        if not folds:
            continue
        expectancies = [row.net_expectancy for row in folds]
        worst_dd = max(row.max_drawdown for row in folds)
        if median(expectancies) <= baseline_expectancy or worst_dd > baseline_worst_dd:
            continue
        dispersion = max(expectancies) - min(expectancies)
        eligible.append((
            -median(expectancies), worst_dd, dispersion,
            identifier.count("&") + 1, identifier,
        ))
    return min(eligible)[-1] if eligible else None
