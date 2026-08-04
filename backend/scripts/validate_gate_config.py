"""Validate coherence of the config-driven L3_PROFILE dataset gates."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


_PARTITIONS = (
    ("train", "ml_catboost_train_size_ratio", "ml_catboost_min_train_samples"),
    (
        "validation",
        "ml_catboost_validation_size_ratio",
        "ml_catboost_min_validation_samples",
    ),
    ("test", "ml_catboost_test_size_ratio", "ml_catboost_min_test_samples"),
)


def _positive_int(config: Mapping[str, Any], key: str) -> int:
    value = config.get(key)
    if isinstance(value, bool):
        raise ValueError(f"invalid_{key}")
    try:
        parsed = int(value)
        exact = float(value) == parsed
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid_{key}") from exc
    if parsed <= 0 or not exact:
        raise ValueError(f"invalid_{key}")
    return parsed


def _fraction(config: Mapping[str, Any], key: str) -> float:
    try:
        parsed = float(config.get(key))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid_{key}") from exc
    if not math.isfinite(parsed) or not 0.0 < parsed < 1.0:
        raise ValueError(f"invalid_{key}")
    return parsed


def validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    total_gate = _positive_int(
        config, "ml_catboost_retrain_min_eligible_rows"
    )
    promotion_test_minimum = _positive_int(
        config, "ml_promotion_min_test_samples"
    )

    ratios: dict[str, float] = {}
    minima: dict[str, int] = {}
    nominal: dict[str, int] = {}
    errors: list[str] = []
    for name, ratio_key, minimum_key in _PARTITIONS:
        ratios[name] = _fraction(config, ratio_key)
        minima[name] = _positive_int(config, minimum_key)
        nominal[name] = math.floor(total_gate * ratios[name])

    if not math.isclose(sum(ratios.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        errors.append("split_ratios_do_not_sum_to_one")
    if sum(minima.values()) > total_gate:
        errors.append("partition_minima_exceed_total_gate")
    for name in ratios:
        if minima[name] > nominal[name]:
            errors.append(f"{name}_minimum_exceeds_nominal_allocation")

    return {
        "valid": not errors,
        "errors": errors,
        "total_gate": total_gate,
        "ratios": ratios,
        "partition_minima": minima,
        "nominal_partition_sizes": nominal,
        "partition_minima_total": sum(minima.values()),
        "pre_purge_headroom": total_gate - sum(minima.values()),
        "promotion_min_test_samples": promotion_test_minimum,
        "nominal_test_meets_promotion_minimum": (
            nominal["test"] >= promotion_test_minimum
        ),
        "promotion_test_nominal_deficit": max(
            0, promotion_test_minimum - nominal["test"]
        ),
    }


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-json", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = json.loads(Path(args.config_json).read_text(encoding="utf-8"))
    result = validate_config(config)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
