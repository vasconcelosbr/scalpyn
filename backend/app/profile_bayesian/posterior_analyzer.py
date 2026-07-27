"""Convert posterior samples into non-causal indicator associations."""

from __future__ import annotations

from typing import Any

import numpy as np


def indicator_posteriors(
    tp_inference: Any,
    pnl_inference: Any,
    feature_names: tuple[str, ...],
) -> list[dict[str, Any]]:
    tp = np.asarray(
        tp_inference.posterior["indicator_effect"].stack(sample=("chain", "draw")).values
    )
    pnl = np.asarray(
        pnl_inference.posterior["indicator_effect"].stack(sample=("chain", "draw")).values
    )
    if tp.shape[0] != len(feature_names):
        tp = tp.T
    if pnl.shape[0] != len(feature_names):
        pnl = pnl.T
    result: list[dict[str, Any]] = []
    for index, name in enumerate(feature_names):
        if name.endswith("__missing"):
            continue
        tp_values = tp[index]
        pnl_values = pnl[index]
        combined = (tp_values > 0).astype(float)
        result.append(
            {
                "indicator": name,
                "effect_direction": (
                    "POSITIVE"
                    if float(np.median(pnl_values)) > 0
                    else "NEGATIVE"
                    if float(np.median(pnl_values)) < 0
                    else "NEUTRAL"
                ),
                "estimated_tp_lift": float(np.median(tp_values)),
                "estimated_pnl_lift": float(np.median(pnl_values)),
                "probability_positive_effect": float(combined.mean()),
                "credible_interval_95": [
                    float(np.quantile(pnl_values, 0.025)),
                    float(np.quantile(pnl_values, 0.975)),
                ],
            }
        )
    return result
