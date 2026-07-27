"""Versioned, lane-specific feature contract for ``L1_SPECTRUM``.

Native point-in-time capture and L1 model eligibility are intentionally
separate concepts:

* native eligibility proves immutable capture, lineage and hash integrity;
* L1 lane eligibility proves the captured row satisfies the active L1 model
  contract.

The active ML config is the single runtime source of truth.  This module is
L1-only and must never alter or interpret the ``L3_PROFILE`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


L1_SOURCE = "L1_SPECTRUM"


class L1FeatureContractConfigError(ValueError):
    """Raised when the active L1 feature contract is missing or inconsistent."""


@dataclass(frozen=True)
class L1FeatureContract:
    version: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    min_row_coverage: float
    ranges: Mapping[str, Mapping[str, float]]
    excluded: tuple[str, ...]

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self.required + self.optional


@dataclass(frozen=True)
class L1FeatureEvaluation:
    contract_version: str
    eligible: bool
    coverage: float
    present_count: int
    expected_count: int
    reasons: tuple[str, ...]
    stale_required_features: tuple[str, ...]


def _names(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise L1FeatureContractConfigError(f"invalid_l1_contract_{field}")
    names = tuple(str(item).strip() for item in value if str(item).strip())
    if not names:
        raise L1FeatureContractConfigError(f"empty_l1_contract_{field}")
    if len(names) != len(set(names)):
        raise L1FeatureContractConfigError(f"duplicate_l1_contract_{field}")
    return names


def load_l1_feature_contract(ml_config: Mapping[str, Any]) -> L1FeatureContract:
    """Load and fail-closed validate the active L1 contract from ML config."""
    version = str(ml_config.get("ml_l1_feature_contract_version") or "").strip()
    if not version:
        raise L1FeatureContractConfigError("missing_l1_feature_contract_version")

    all_contracts = ml_config.get("ml_feature_contract")
    if not isinstance(all_contracts, Mapping):
        raise L1FeatureContractConfigError("missing_ml_feature_contract")
    lane = all_contracts.get(L1_SOURCE)
    if not isinstance(lane, Mapping):
        raise L1FeatureContractConfigError("missing_l1_feature_contract")
    lane_version = str(lane.get("version") or "").strip()
    if lane_version != version:
        raise L1FeatureContractConfigError(
            f"l1_feature_contract_version_mismatch:{lane_version or 'missing'}"
            f"!={version}"
        )

    required = _names(lane.get("required"), field="required")
    optional = _names(lane.get("optional"), field="optional")
    overlap = sorted(set(required) & set(optional))
    if overlap:
        raise L1FeatureContractConfigError(
            "overlapping_l1_contract_features:" + ",".join(overlap)
        )

    try:
        min_row_coverage = float(lane["min_row_coverage"])
    except (KeyError, TypeError, ValueError) as exc:
        raise L1FeatureContractConfigError(
            "missing_or_invalid_l1_min_row_coverage"
        ) from exc
    if not 0.0 < min_row_coverage <= 1.0:
        raise L1FeatureContractConfigError("invalid_l1_min_row_coverage")

    excluded_raw = ml_config.get("ml_l1_feature_exclusions") or []
    if not isinstance(excluded_raw, (list, tuple)):
        raise L1FeatureContractConfigError("invalid_ml_l1_feature_exclusions")
    excluded = tuple(str(item).strip() for item in excluded_raw if str(item).strip())
    excluded_overlap = sorted(set(required + optional) & set(excluded))
    if excluded_overlap:
        raise L1FeatureContractConfigError(
            "l1_contract_feature_also_excluded:" + ",".join(excluded_overlap)
        )

    all_ranges = ml_config.get("ml_feature_ranges") or {}
    if not isinstance(all_ranges, Mapping):
        raise L1FeatureContractConfigError("invalid_ml_feature_ranges")
    lane_ranges = {
        name: rules
        for name, rules in all_ranges.items()
        if name in set(required + optional) and isinstance(rules, Mapping)
    }

    return L1FeatureContract(
        version=version,
        required=required,
        optional=optional,
        min_row_coverage=min_row_coverage,
        ranges=lane_ranges,
        excluded=excluded,
    )


def _range_errors(
    snapshot: Mapping[str, Any],
    contract: L1FeatureContract,
) -> list[str]:
    errors: list[str] = []
    for name, rules in contract.ranges.items():
        value = snapshot.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"invalid_numeric:{name}")
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            errors.append(f"non_finite:{name}")
            continue
        if "gt" in rules and not numeric > float(rules["gt"]):
            errors.append(f"range_gt:{name}")
        if "gte" in rules and not numeric >= float(rules["gte"]):
            errors.append(f"range_gte:{name}")
        if "lt" in rules and not numeric < float(rules["lt"]):
            errors.append(f"range_lt:{name}")
        if "lte" in rules and not numeric <= float(rules["lte"]):
            errors.append(f"range_lte:{name}")
    return errors


def evaluate_l1_snapshot(
    snapshot: Mapping[str, Any],
    ml_config: Mapping[str, Any] | None,
    *,
    feature_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> L1FeatureEvaluation:
    """Evaluate one immutable entry snapshot against the active L1 contract."""
    try:
        contract = load_l1_feature_contract(ml_config or {})
    except L1FeatureContractConfigError as exc:
        return L1FeatureEvaluation(
            contract_version="UNCONFIGURED",
            eligible=False,
            coverage=0.0,
            present_count=0,
            expected_count=0,
            reasons=(str(exc),),
            stale_required_features=(),
        )

    names = contract.feature_names
    present_count = sum(snapshot.get(name) is not None for name in names)
    coverage = present_count / len(names)
    reasons = [
        f"missing_required:{name}"
        for name in contract.required
        if snapshot.get(name) is None
    ]
    reasons.extend(_range_errors(snapshot, contract))
    if coverage < contract.min_row_coverage:
        reasons.append(
            "row_coverage_below_min:"
            f"{coverage:.6f}<{contract.min_row_coverage:.6f}"
        )

    metadata = feature_metadata or {}
    stale_required = tuple(
        name
        for name in contract.required
        if bool((metadata.get(name) or {}).get("stale"))
    )
    reasons.extend(f"stale_required:{name}" for name in stale_required)

    unique_reasons = tuple(dict.fromkeys(reasons))
    return L1FeatureEvaluation(
        contract_version=contract.version,
        eligible=not unique_reasons,
        coverage=coverage,
        present_count=present_count,
        expected_count=len(names),
        reasons=unique_reasons,
        stale_required_features=stale_required,
    )
