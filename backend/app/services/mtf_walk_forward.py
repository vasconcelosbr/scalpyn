"""Leakage-resistant chronological walk-forward primitives for Spot MTF."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import math
from statistics import NormalDist
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Sequence


class MTFCalibrationConfigRequired(ValueError):
    code = "CONFIG_REQUIRED"


@dataclass(frozen=True)
class FoldResult:
    net_expectancy: float
    max_drawdown: float
    samples: int
    population_samples: int = 0


_REQUIRED_POLICY_FIELDS = (
    "policy_version", "approval_status", "approved_at", "approved_by",
    "min_samples", "fold_count", "train_window_rows", "test_window_rows",
    "embargo_rows", "validation_holdout_rows", "min_test_samples_per_fold",
    "candidate_quantiles", "candidate_dimensions", "max_candidates",
    "confidence_level", "observation_min_samples", "observation_min_hours",
    "cost_field", "return_field", "profile_templates", "scope",
    "l2_history_candles",
    "sampling_unit", "overlap_policy", "capital_policy", "execution_policy",
    "max_dataset_rows", "dataset_query_timeout_ms",
)


def require_calibration_config(config: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in _REQUIRED_POLICY_FIELDS if config.get(key) is None]
    if missing:
        raise MTFCalibrationConfigRequired("CONFIG_REQUIRED:" + ",".join(missing))
    parsed = dict(config)
    if parsed["approval_status"] != "APPROVED":
        raise MTFCalibrationConfigRequired("CONFIG_REQUIRED:approval_status")
    integer_fields = (
        "min_samples", "fold_count", "train_window_rows", "test_window_rows",
        "validation_holdout_rows", "min_test_samples_per_fold", "max_candidates",
        "observation_min_samples", "observation_min_hours",
        "l2_history_candles",
        "max_dataset_rows", "dataset_query_timeout_ms",
    )
    if any(int(parsed[key]) <= 0 for key in integer_fields):
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:positive_integer")
    if int(parsed["embargo_rows"]) < 0:
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:embargo_rows")
    if not 0 < float(parsed["confidence_level"]) < 1:
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:confidence_level")
    quantiles = [float(value) for value in parsed["candidate_quantiles"]]
    if not quantiles or any(value <= 0 or value >= 1 for value in quantiles):
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:candidate_quantiles")
    dimensions = list(parsed["candidate_dimensions"] or [])
    if not dimensions:
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:candidate_dimensions")
    search_mode = str(parsed.get("candidate_search_mode") or "CARTESIAN")
    if search_mode not in {"CARTESIAN", "BOUNDED_COORDINATE"}:
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:candidate_search_mode")
    option_count = 1
    quantile_dimension_count = 0
    discrete_option_count = 1
    for item in dimensions:
        mode = str(item.get("mode") or "quantile")
        applies_to = str(item.get("applies_to") or "BOTH")
        if (
            item.get("layer") not in {"L1", "L2"}
            or mode not in {"quantile", "discrete"}
            or applies_to not in {"FILTER", "SEMANTIC", "BOTH"}
            or (applies_to in {"FILTER", "BOTH"} and not item.get("feature"))
            or (mode == "quantile" and not item.get("feature"))
            or (item.get("feature") and item.get("operator") not in {"min", "max"})
            or (applies_to in {"SEMANTIC", "BOTH"} and not item.get("semantic_key"))
        ):
            raise MTFCalibrationConfigRequired("CONFIG_INVALID:candidate_dimensions")
        if mode == "discrete":
            values = list(item.get("values") or [])
            if not values or any(
                isinstance(value, bool) or not math.isfinite(float(value))
                for value in values
            ):
                raise MTFCalibrationConfigRequired(
                    "CONFIG_INVALID:candidate_dimension_values"
                )
            option_count *= len(values)
            discrete_option_count *= len(values)
        else:
            option_count *= len(quantiles)
            quantile_dimension_count += 1
    if search_mode == "BOUNDED_COORDINATE":
        option_count = discrete_option_count * (
            1 + quantile_dimension_count * max(0, len(quantiles) - 1)
        )
    if option_count > int(parsed["max_candidates"]):
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:candidate_budget_exceeded")
    scope = dict(parsed["scope"] or {})
    if (
        scope.get("market_type") != "spot"
        or scope.get("timeframes") != {"L1": "1h", "L2": "15m", "L3": "5m"}
    ):
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:scope")
    templates = dict(parsed["profile_templates"] or {})
    if set(templates) != {"L1", "L2"}:
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:profile_templates")
    expected_timeframes = {"L1": "1h", "L2": "15m"}
    required_semantics = {
        "L1": {"adx_strong_min", "atr_pct_low", "atr_pct_high"},
        "L2": {
            "max_extension_atr", "pullback_max_distance_atr",
            "breakout_min_distance_atr", "retest_tolerance_atr",
            "invalidation_atr", "setup_valid_candles", "adx_impulse_min",
            "volume_relative_min", "bb_width_compression_max",
            "bb_width_expansion_min",
        },
    }
    calibrated_semantics = {
        layer: {
            str(item.get("semantic_key"))
            for item in dimensions
            if item.get("layer") == layer and item.get("semantic_key")
        }
        for layer in ("L1", "L2")
    }
    for layer, template in templates.items():
        if template.get("default_timeframe") != expected_timeframes[layer]:
            raise MTFCalibrationConfigRequired("CONFIG_INVALID:profile_template_timeframe")
        semantics = template.get("mtf_semantics") or {}
        if not required_semantics[layer].issubset(calibrated_semantics[layer]):
            raise MTFCalibrationConfigRequired(
                f"CONFIG_INVALID:{layer.lower()}_calibrated_semantics"
            )
        if not required_semantics[layer].issubset(set(semantics)):
            raise MTFCalibrationConfigRequired(
                f"CONFIG_INVALID:{layer.lower()}_template_semantics"
            )
        source = template.get("source_identity") or {}
        if (
            source.get("candle_policy") != "CLOSED_ONLY"
            or source.get("validity_margin_seconds") is None
            or not source.get("allowed_source_providers")
            or not source.get("provider_policy_id")
            or not source.get("allowed_capture_contract_versions")
            or source.get("scheduler_group") != "structural"
            or not source.get("allowed_producer_versions")
            or not source.get("indicator_config_profile_id")
            or not source.get("indicator_config_hash")
        ):
            raise MTFCalibrationConfigRequired(
                f"CONFIG_INVALID:{layer.lower()}_source_identity"
            )
    setup_dimension = next(
        (
            item for item in dimensions
            if item.get("layer") == "L2"
            and item.get("semantic_key") == "setup_valid_candles"
        ),
        None,
    )
    if (
        setup_dimension is None
        or str(setup_dimension.get("mode") or "quantile") != "discrete"
        or any(
            int(value) != float(value)
            or int(value) <= 0
            or int(value) >= int(parsed["l2_history_candles"])
            for value in setup_dimension.get("values") or []
        )
    ):
        raise MTFCalibrationConfigRequired(
            "CONFIG_INVALID:l2_setup_history_window"
        )
    requested = (
        int(parsed["train_window_rows"])
        + int(parsed["test_window_rows"]) * int(parsed["fold_count"])
        + int(parsed["embargo_rows"]) * int(parsed["fold_count"])
        + int(parsed["validation_holdout_rows"])
    )
    if requested < int(parsed["min_samples"]):
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:sample_window_below_minimum")
    if requested > int(parsed["max_dataset_rows"]):
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:dataset_row_budget_exceeded")
    if not str(parsed["cost_field"]).strip() or not str(parsed["return_field"]).strip():
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:cost_return_fields")
    if parsed["sampling_unit"] != "SHADOW_DECISION":
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:sampling_unit")
    if parsed["overlap_policy"] != "PURGE_TRAIN_OUTCOMES_AT_TEST_BOUNDARY":
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:overlap_policy")
    capital_policy = dict(parsed["capital_policy"] or {})
    if capital_policy.get("mode") not in {
        "EQUAL_NOTIONAL_RETURN", "HISTORICAL_NOTIONAL_RETURN",
    }:
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:capital_policy")
    execution_policy = dict(parsed["execution_policy"] or {})
    if (
        execution_policy.get("mode") != "PERSISTED_POINT_IN_TIME"
        or execution_policy.get("return_field") != parsed["return_field"]
        or execution_policy.get("cost_field") != parsed["cost_field"]
    ):
        raise MTFCalibrationConfigRequired("CONFIG_INVALID:execution_policy")
    parsed["candidate_quantiles"] = quantiles
    parsed["candidate_dimensions"] = dimensions
    parsed["candidate_search_mode"] = search_mode
    return parsed


def chronological_folds(
    rows: Sequence[Mapping[str, Any]], *, train_size: int, test_size: int,
    fold_count: int, embargo_rows: int = 0,
) -> list[tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]]:
    """Build folds without splitting simultaneous decisions or open outcomes."""
    ordered = sorted(rows, key=lambda row: (row["decision_at"], str(row.get("id", ""))))
    folds: list[tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]] = []
    for fold in range(fold_count):
        test_end = len(ordered) - (fold_count - fold - 1) * test_size
        while test_end < len(ordered) and test_end > 0 and (
            ordered[test_end - 1]["decision_at"] == ordered[test_end]["decision_at"]
        ):
            test_end -= 1
        test_start = test_end - test_size
        while 0 < test_start < len(ordered) and (
            ordered[test_start - 1]["decision_at"] == ordered[test_start]["decision_at"]
        ):
            test_start -= 1
        train_end = max(0, test_start - embargo_rows)
        train_start = max(0, train_end - train_size)
        if train_start >= train_end or test_start < 0 or test_start >= test_end:
            continue
        test_start_at = ordered[test_start]["decision_at"]
        train = [
            row for row in ordered[train_start:train_end]
            if row.get("outcome_at") is None or row["outcome_at"] < test_start_at
        ]
        test = ordered[test_start:test_end]
        if train and test:
            folds.append((train, test))
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


def evaluate_returns(rows: Sequence[Mapping[str, Any]]) -> FoldResult:
    returns = [float(row["net_return"]) for row in rows]
    return FoldResult(
        net_expectancy=(sum(returns) / len(returns)) if returns else float("nan"),
        max_drawdown=max_drawdown(returns),
        samples=len(returns),
        population_samples=len(returns),
    )


def evaluate_candidate(
    candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
    *, temporal_eligibility: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None,
) -> FoldResult:
    """Evaluate on the baseline population; a rejected opportunity returns zero."""
    selected = [
        candidate_passes(candidate, row)
        and (temporal_eligibility(candidate, row) if temporal_eligibility else True)
        for row in rows
    ]
    returns = [
        float(row["net_return"]) if accepted else 0.0
        for row, accepted in zip(rows, selected)
    ]
    return FoldResult(
        net_expectancy=(sum(returns) / len(returns)) if returns else float("nan"),
        max_drawdown=max_drawdown(returns),
        samples=sum(selected),
        population_samples=len(returns),
    )


def expectancy_delta_confidence_interval(
    candidate_folds: Sequence[FoldResult],
    baseline_folds: Sequence[FoldResult],
    *,
    confidence_level: float,
) -> tuple[float, float]:
    """Normal interval over paired OOS fold expectancy deltas."""
    if len(candidate_folds) != len(baseline_folds) or len(candidate_folds) < 2:
        return float("nan"), float("nan")
    deltas = [
        candidate.net_expectancy - baseline.net_expectancy
        for candidate, baseline in zip(candidate_folds, baseline_folds)
    ]
    mean = sum(deltas) / len(deltas)
    variance = sum((value - mean) ** 2 for value in deltas) / (len(deltas) - 1)
    standard_error = math.sqrt(variance / len(deltas))
    z_score = NormalDist().inv_cdf((1.0 + float(confidence_level)) / 2.0)
    return mean - z_score * standard_error, mean + z_score * standard_error


def empirical_quantile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        raise ValueError("CANDIDATE_FEATURE_UNAVAILABLE")
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def candidate_grid(policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    dimensions = list(policy["candidate_dimensions"])
    if str(policy.get("candidate_search_mode") or "CARTESIAN") == "BOUNDED_COORDINATE":
        quantile_dimensions = [
            dict(item) for item in dimensions
            if str(item.get("mode") or "quantile") != "discrete"
        ]
        discrete_dimensions = [
            dict(item) for item in dimensions
            if str(item.get("mode") or "quantile") == "discrete"
        ]
        quantiles = [float(value) for value in policy["candidate_quantiles"]]
        base_quantile = quantiles[0]
        quantile_vectors = [
            [base_quantile for _ in quantile_dimensions]
        ]
        for index in range(len(quantile_dimensions)):
            for quantile in quantiles[1:]:
                vector = [base_quantile for _ in quantile_dimensions]
                vector[index] = quantile
                quantile_vectors.append(vector)
        discrete_options = [
            list(item.get("values") or []) for item in discrete_dimensions
        ]
        discrete_combinations = list(product(*discrete_options)) if discrete_options else [()]
        candidates: list[dict[str, Any]] = []
        for vector in quantile_vectors:
            for discrete_values in discrete_combinations:
                rules = [
                    {**item, "quantile": vector[index]}
                    for index, item in enumerate(quantile_dimensions)
                ] + [
                    {**item, "value": discrete_values[index]}
                    for index, item in enumerate(discrete_dimensions)
                ]
                identifier = "&".join(
                    f"{rule['layer']}:{rule.get('feature') or rule['semantic_key']}:"
                    + (
                        f"{rule.get('operator') or 'set'}@v{rule['value']}"
                        if "value" in rule
                        else f"{rule['operator']}@q{rule['quantile']:.6f}"
                    )
                    for rule in rules
                )
                candidates.append({"id": identifier, "rules": rules})
        if len(candidates) > int(policy["max_candidates"]):
            raise ValueError("CANDIDATE_BUDGET_EXCEEDED")
        return candidates
    candidates: list[dict[str, Any]] = []
    options = [
        [
            {**dict(dimension), "value": value}
            for value in dimension.get("values") or []
        ]
        if str(dimension.get("mode") or "quantile") == "discrete"
        else [
            {**dict(dimension), "quantile": float(quantile)}
            for quantile in policy["candidate_quantiles"]
        ]
        for dimension in dimensions
    ]
    for combination in product(*options):
        rules = [dict(rule) for rule in combination]
        identifier = "&".join(
            (
                f"{rule['layer']}:{rule.get('feature') or rule['semantic_key']}:"
                f"{rule.get('operator') or 'set'}@v{rule['value']}"
                if "value" in rule
                else f"{rule['layer']}:{rule['feature']}:{rule['operator']}@q{rule['quantile']:.6f}"
            )
            for rule in rules
        )
        candidates.append({"id": identifier, "rules": rules})
    if len(candidates) > int(policy["max_candidates"]):
        raise ValueError("CANDIDATE_BUDGET_EXCEEDED")
    return candidates


def fit_candidate(
    template: Mapping[str, Any], train: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    fitted = {"id": template["id"], "rules": []}
    for rule in template["rules"]:
        if "value" in rule:
            threshold = rule["value"]
        else:
            values = [row["features"].get(rule["feature"]) for row in train]
            values = [float(value) for value in values if value is not None and math.isfinite(float(value))]
            threshold = empirical_quantile(values, float(rule["quantile"]))
        fitted["rules"].append({**dict(rule), "threshold": threshold})
    return fitted


def candidate_passes(candidate: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    for rule in candidate["rules"]:
        if str(rule.get("applies_to") or "BOTH") == "SEMANTIC":
            continue
        value = row["features"].get(rule["feature"])
        if value is None or not math.isfinite(float(value)):
            return False
        if rule["operator"] == "min" and float(value) < float(rule["threshold"]):
            return False
        if rule["operator"] == "max" and float(value) > float(rule["threshold"]):
            return False
    return True


def select_candidate(
    candidate_folds: Mapping[str, Sequence[FoldResult]],
    *, baseline_folds: Sequence[FoldResult],
) -> str | None:
    """Select by OOS median, then worst DD, dispersion, and complexity."""
    if not baseline_folds:
        return None
    baseline_expectancy = median(row.net_expectancy for row in baseline_folds)
    baseline_worst_dd = max(row.max_drawdown for row in baseline_folds)
    eligible = []
    for identifier, folds in candidate_folds.items():
        if not folds or any(not math.isfinite(row.net_expectancy) for row in folds):
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
