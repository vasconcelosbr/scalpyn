"""Out-of-sample indicator intelligence at independent market-snapshot grain."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Sequence

import numpy as np


def inverse_group_frequency_weights(group_keys: Iterable[str]) -> np.ndarray:
    keys = [str(key) for key in group_keys]
    counts = Counter(keys)
    return np.asarray([1.0 / counts[key] for key in keys], dtype=float)


def _weighted_mean(values, weights) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    return float(np.average(values, weights=weights)) if weights.sum() > 0 else 0.0


def build_indicator_intelligence_report(
    X_train,
    y_train,
    X_val,
    y_val,
    X_test,
    y_test,
    feature_names: Sequence[str],
    train_weights,
    val_weights,
    test_weights,
    val_returns,
    test_returns,
    *,
    min_effective_cases: float,
    min_abs_lift: float,
    label: str = "positive_net_return",
) -> dict[str, Any]:
    """Find stable feature buckets using train-only cut points and two holdouts."""
    X_train = np.asarray(X_train, dtype=float)
    X_val = np.asarray(X_val, dtype=float)
    X_test = np.asarray(X_test, dtype=float)
    y_train = np.asarray(y_train, dtype=float)
    y_val = np.asarray(y_val, dtype=float)
    y_test = np.asarray(y_test, dtype=float)
    train_weights = np.asarray(train_weights, dtype=float)
    val_weights = np.asarray(val_weights, dtype=float)
    test_weights = np.asarray(test_weights, dtype=float)
    val_returns = np.asarray(val_returns, dtype=float)
    test_returns = np.asarray(test_returns, dtype=float)
    baselines = {
        "train": _weighted_mean(y_train, train_weights),
        "validation": _weighted_mean(y_val, val_weights),
        "test": _weighted_mean(y_test, test_weights),
    }
    findings: list[dict[str, Any]] = []
    for column, name in enumerate(feature_names):
        train_values = X_train[:, column]
        finite_train = train_values[np.isfinite(train_values)]
        if finite_train.size < 4:
            continue
        edges = np.unique(np.quantile(finite_train, [0.25, 0.50, 0.75]))
        if edges.size < 2:
            continue
        for bucket in range(edges.size + 1):
            masks = {}
            for split_name, matrix in (("validation", X_val), ("test", X_test)):
                values = matrix[:, column]
                masks[split_name] = np.isfinite(values) & (np.digitize(values, edges) == bucket)
            val_effective = float(val_weights[masks["validation"]].sum())
            test_effective = float(test_weights[masks["test"]].sum())
            if min(val_effective, test_effective) < min_effective_cases:
                continue
            val_rate = _weighted_mean(y_val[masks["validation"]], val_weights[masks["validation"]])
            test_rate = _weighted_mean(y_test[masks["test"]], test_weights[masks["test"]])
            val_lift = val_rate - baselines["validation"]
            test_lift = test_rate - baselines["test"]
            if val_lift >= min_abs_lift and test_lift >= min_abs_lift:
                action = "PRIORITIZE"
            elif val_lift <= -min_abs_lift and test_lift <= -min_abs_lift:
                action = "BLOCK_CANDIDATE"
            else:
                action = "OBSERVE"
            lower = None if bucket == 0 else float(edges[bucket - 1])
            upper = None if bucket == edges.size else float(edges[bucket])
            findings.append({
                "indicator": name,
                "bucket": {"lower_exclusive": lower, "upper_inclusive": upper},
                "action": action,
                "validation": {
                    "effective_cases": val_effective,
                    "positive_rate": val_rate,
                    "lift": val_lift,
                    "net_return_pct": _weighted_mean(
                        val_returns[masks["validation"]], val_weights[masks["validation"]]
                    ),
                },
                "test": {
                    "effective_cases": test_effective,
                    "positive_rate": test_rate,
                    "lift": test_lift,
                    "net_return_pct": _weighted_mean(
                        test_returns[masks["test"]], test_weights[masks["test"]]
                    ),
                },
            })
    findings.sort(key=lambda item: abs(item["test"]["lift"]), reverse=True)
    return {
        "scope": "global_market_snapshot",
        "label": label,
        "execution_authority": False,
        "baselines": baselines,
        "min_effective_cases": float(min_effective_cases),
        "min_abs_lift": float(min_abs_lift),
        "findings": findings,
    }
