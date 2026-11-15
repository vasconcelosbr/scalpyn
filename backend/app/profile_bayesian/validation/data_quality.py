"""Missingness checks stratified by day and entry ATR bucket."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from math import sqrt
from typing import Any, Sequence

import numpy as np

from ..data_contract import CanonicalObservation


def _normalized_outcome(value: str) -> str:
    if value in {"TP", "TP_HIT"}:
        return "TP_HIT"
    if value in {"SL", "SL_HIT"}:
        return "SL_HIT"
    return "TIMEOUT"


def _atr_bucket(value: float | None, edges: Sequence[float]) -> str:
    if value is None or not np.isfinite(value):
        return "MISSING"
    position = bisect_right(edges, value)
    if not edges:
        return "ALL_FINITE"
    if position == 0:
        return f"<={edges[0]:g}"
    if position >= len(edges):
        return f">{edges[-1]:g}"
    return f"({edges[position - 1]:g},{edges[position]:g}]"


def _missing_outcome_cramers_v(
    observations: Sequence[CanonicalObservation], feature: str
) -> float:
    outcomes = ("SL_HIT", "TIMEOUT", "TP_HIT")
    table = np.zeros((2, len(outcomes)), dtype=float)
    outcome_index = {value: index for index, value in enumerate(outcomes)}
    for item in observations:
        value = item.indicators.get(feature)
        missing = int(value is None or not np.isfinite(value))
        table[missing, outcome_index[_normalized_outcome(item.outcome)]] += 1
    total = float(table.sum())
    if total == 0 or np.count_nonzero(table.sum(axis=1)) < 2:
        return 0.0
    expected = np.outer(table.sum(axis=1), table.sum(axis=0)) / total
    nonzero = expected > 0
    chi_square = float((((table - expected) ** 2)[nonzero] / expected[nonzero]).sum())
    denominator = total * min(table.shape[0] - 1, table.shape[1] - 1)
    return sqrt(chi_square / denominator) if denominator > 0 else 0.0


def stratified_feature_quality(
    observations: Sequence[CanonicalObservation],
    *,
    atr_bucket_edges_pct: Sequence[float],
    min_global_coverage: float,
    min_group_samples: int,
    max_missing_outcome_cramers_v: float,
) -> dict[str, Any]:
    if not observations:
        return {"features": {}, "violations": ["empty_observations"]}
    feature_names = sorted(
        {name for item in observations for name in item.indicators}
    )
    groups: dict[tuple[str, str], list[CanonicalObservation]] = defaultdict(list)
    for item in observations:
        groups[
            (
                item.occurred_at.date().isoformat(),
                _atr_bucket(item.atr_pct_at_entry, atr_bucket_edges_pct),
            )
        ].append(item)
    eligible_groups = {
        key: values
        for key, values in groups.items()
        if len(values) >= min_group_samples
    }
    features: dict[str, Any] = {}
    violations: list[str] = []
    for feature in feature_names:
        present = [
            item.indicators.get(feature) is not None
            and np.isfinite(item.indicators[feature])
            for item in observations
        ]
        global_coverage = float(np.mean(present))
        group_coverage = {
            f"{day}|{bucket}": float(
                np.mean(
                    [
                        item.indicators.get(feature) is not None
                        and np.isfinite(item.indicators[feature])
                        for item in items
                    ]
                )
            )
            for (day, bucket), items in eligible_groups.items()
        }
        min_stratified = (
            min(group_coverage.values())
            if group_coverage
            else global_coverage
        )
        cramers_v = _missing_outcome_cramers_v(observations, feature)
        candidate = global_coverage >= min_global_coverage
        feature_violations = []
        if candidate and min_stratified < min_global_coverage:
            feature_violations.append("stratified_coverage_below_policy")
        if candidate and cramers_v > max_missing_outcome_cramers_v:
            feature_violations.append("missingness_associated_with_outcome")
        if feature_violations:
            violations.extend(
                f"{feature}:{violation}" for violation in feature_violations
            )
        features[feature] = {
            "candidate_for_model": candidate,
            "global_coverage": global_coverage,
            "min_stratified_coverage": min_stratified,
            "missing_outcome_cramers_v": cramers_v,
            "eligible_group_count": len(eligible_groups),
            "violations": feature_violations,
        }
    return {
        "stratification": "entry_day_x_atr_pct_at_entry_bucket",
        "min_group_samples": min_group_samples,
        "eligible_group_count": len(eligible_groups),
        "features": features,
        "violations": sorted(violations),
    }
