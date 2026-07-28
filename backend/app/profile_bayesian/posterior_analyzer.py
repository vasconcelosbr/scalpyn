"""Convert posterior samples into non-causal indicator associations."""

from __future__ import annotations

from typing import Any

import numpy as np


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=1, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / exponentiated.sum(axis=1, keepdims=True)


def indicator_ev_posteriors(
    outcome_inference: Any,
    pnl_inference: Any,
    feature_names: tuple[str, ...],
    *,
    practical_effect_rope_pct: float,
) -> list[dict[str, Any]]:
    """Derive coherent net-EV lifts from the outcome and conditional-PnL models."""

    outcome_beta = (
        outcome_inference.posterior["indicator_outcome_effect"]
        .stack(sample=("chain", "draw"))
        .transpose("feature", "outcome_logit", "sample")
        .values
    )
    outcome_intercept = (
        outcome_inference.posterior["outcome_intercept"]
        .stack(sample=("chain", "draw"))
        .transpose("sample", "outcome_logit")
        .values
    )
    pnl_intercept = (
        pnl_inference.posterior["pnl_outcome_intercept"]
        .stack(sample=("chain", "draw"))
        .transpose("sample", "outcome")
        .values
    )
    if "indicator_pnl_effect" in pnl_inference.posterior:
        pnl_beta = (
            pnl_inference.posterior["indicator_pnl_effect"]
            .stack(sample=("chain", "draw"))
            .transpose("feature", "outcome", "sample")
            .values
        )
    else:
        # In the contract-aware parameterization, indicator associations enter
        # through outcome probabilities. Conditional magnitude has only
        # outcome intercepts because TP/SL magnitudes are deterministic and
        # TIMEOUT has insufficient within-class support for feature slopes.
        pnl_beta = np.zeros(
            (len(feature_names), pnl_intercept.shape[1], pnl_intercept.shape[0]),
            dtype=float,
        )
    reference_logits = np.column_stack(
        [np.zeros(outcome_intercept.shape[0]), outcome_intercept]
    )
    reference_probability = _softmax(reference_logits)
    reference_ev = np.sum(reference_probability * pnl_intercept, axis=1)
    results: list[dict[str, Any]] = []
    for index, name in enumerate(feature_names):
        if name.endswith("__missing"):
            continue
        shifted_logits = reference_logits.copy()
        shifted_logits[:, 1:] += outcome_beta[index].T
        shifted_probability = _softmax(shifted_logits)
        shifted_pnl = pnl_intercept + pnl_beta[index].T
        shifted_ev = np.sum(shifted_probability * shifted_pnl, axis=1)
        ev_lift = shifted_ev - reference_ev
        tp_lift = shifted_probability[:, 2] - reference_probability[:, 2]
        median_ev_lift = float(np.median(ev_lift))
        results.append(
            {
                "indicator": name,
                "effect_direction": (
                    "POSITIVE"
                    if median_ev_lift > practical_effect_rope_pct
                    else "NEGATIVE"
                    if median_ev_lift < -practical_effect_rope_pct
                    else "NEUTRAL"
                ),
                "estimated_tp_lift": float(np.median(tp_lift)),
                "estimated_pnl_lift": median_ev_lift,
                "probability_positive_effect": float(
                    np.mean(ev_lift > practical_effect_rope_pct)
                ),
                "probability_negative_effect": float(
                    np.mean(ev_lift < -practical_effect_rope_pct)
                ),
                "probability_practically_equivalent": float(
                    np.mean(
                        np.abs(ev_lift)
                        <= practical_effect_rope_pct
                    )
                ),
                "credible_interval_95": [
                    float(np.quantile(ev_lift, 0.025)),
                    float(np.quantile(ev_lift, 0.975)),
                ],
                "estimand": "standardized_global_net_ev_lift",
                "rope_pct": practical_effect_rope_pct,
            }
        )
    return results


def stable_effect_windows(
    discovery: list[dict[str, Any]],
    validation: list[dict[str, Any]],
) -> dict[str, int]:
    validation_by_indicator = {item["indicator"]: item for item in validation}
    stable: dict[str, int] = {}
    for effect in discovery:
        other = validation_by_indicator.get(effect["indicator"])
        stable[effect["indicator"]] = (
            2
            if other is not None
            and effect["effect_direction"] == other["effect_direction"]
            and effect["effect_direction"] != "NEUTRAL"
            else 0
        )
    return stable


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
