"""Derive bounded search dimensions from the current immutable version."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ...services.calibration_orchestrator_v2 import resolve_stable_path


class SearchSpaceError(ValueError):
    pass


@dataclass(frozen=True)
class SearchDimension:
    target_path: str
    current_value: float
    low: float
    high: float
    step: float | None
    value_type: str


def build_search_space(
    current_config: Mapping[str, Any],
    authorized_policy: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    dimensions: list[SearchDimension] = []
    for target_path, limits in authorized_policy.items():
        current = resolve_stable_path(current_config, target_path)
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise SearchSpaceError(f"{target_path} is not a numeric current value")
        allowed_min = float(limits["min"])
        allowed_max = float(limits["max"])
        max_absolute_delta = float(limits["max_absolute_delta"])
        low = max(allowed_min, float(current) - max_absolute_delta)
        high = min(allowed_max, float(current) + max_absolute_delta)
        if low >= high:
            raise SearchSpaceError(f"{target_path} has an empty bounded range")
        dimensions.append(
            SearchDimension(
                target_path=target_path,
                current_value=float(current),
                low=low,
                high=high,
                step=float(limits["step"]) if limits.get("step") is not None else None,
                value_type=str(limits.get("type") or "float"),
            )
        )
    return [asdict(item) for item in dimensions]


def suggest_parameters(trial: Any, dimensions: list[Mapping[str, Any]]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for item in dimensions:
        path = str(item["target_path"])
        if item["value_type"] == "int":
            parameters[path] = trial.suggest_int(
                path,
                int(item["low"]),
                int(item["high"]),
                step=int(item["step"] or 1),
            )
        else:
            parameters[path] = trial.suggest_float(
                path,
                float(item["low"]),
                float(item["high"]),
                step=float(item["step"]) if item.get("step") else None,
            )
    return parameters
