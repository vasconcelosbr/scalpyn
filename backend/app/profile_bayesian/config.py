"""Fail-closed flags, policy loading, and operational authority."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class FeatureFlags:
    enabled: bool
    analysis_enabled: bool
    optimization_enabled: bool
    candidate_creation_enabled: bool
    shadow_submission_enabled: bool
    auto_promotion_enabled: bool = False


def feature_flags() -> FeatureFlags:
    """Load flags at request/task time so disabling does not require re-import."""
    return FeatureFlags(
        enabled=_env_true("PROFILE_BAYESIAN_ENABLED"),
        analysis_enabled=_env_true("PROFILE_BAYESIAN_ANALYSIS_ENABLED"),
        optimization_enabled=_env_true("PROFILE_BAYESIAN_OPTIMIZATION_ENABLED"),
        candidate_creation_enabled=_env_true(
            "PROFILE_BAYESIAN_CANDIDATE_CREATION_ENABLED"
        ),
        shadow_submission_enabled=_env_true(
            "PROFILE_BAYESIAN_SHADOW_SUBMISSION_ENABLED"
        ),
        # Deliberately ignore the environment. This implementation has no
        # automatic-promotion authority.
        auto_promotion_enabled=False,
    )


@dataclass(frozen=True)
class OperationalAuthority:
    analysis: bool = True
    recommendation: bool = True
    candidate_creation: str = "FEATURE_FLAG"
    profile_mutation: bool = False
    trade_decision: bool = False
    order_execution: bool = False
    ml_training: bool = False
    ml_promotion: bool = False
    automatic_activation: bool = False


AUTHORITY = OperationalAuthority()


REQUIRED_POLICY_KEYS = frozenset(
    {
        "max_trades",
        "max_runtime_seconds",
        "max_draws",
        "max_tune",
        "max_trials",
        "max_workers",
        "max_candidates",
        "max_changes_per_candidate",
        "min_trades",
        "min_direct_samples",
        "min_symbols",
        "min_days",
        "max_symbol_concentration",
        "max_drawdown",
        "min_expectancy_oos",
        "min_profit_factor",
        "max_is_oos_degradation",
        "min_regime_samples",
        "credible_interval",
        "max_rhat",
        "min_effective_sample_size",
        "max_divergences",
        "min_feature_coverage",
        "sampler_config",
        "evidence_grading",
        "split_config",
        "objective_weights",
        "authorized_search_space",
        "permissions",
    }
)

ANALYSIS_ONLY_TEMPLATE_ID = "analysis_only_v1"
PERMISSION_KEYS = frozenset(
    {
        "profile_bayesian.view",
        "profile_bayesian.run_analysis",
        "profile_bayesian.run_optimization",
        "profile_bayesian.create_candidate",
        "profile_bayesian.submit_replay",
        "profile_bayesian.submit_shadow",
        "profile_bayesian.approve_candidate",
    }
)
MUTATING_PERMISSIONS = frozenset(
    {
        "profile_bayesian.run_optimization",
        "profile_bayesian.create_candidate",
        "profile_bayesian.submit_replay",
        "profile_bayesian.submit_shadow",
        "profile_bayesian.approve_candidate",
    }
)


class PolicyConfigurationError(ValueError):
    pass


class _StrictPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SamplerConfig(_StrictPolicyModel):
    draws: int = Field(gt=0)
    tune: int = Field(gt=0)
    chains: int = Field(ge=2)
    cores: int = Field(gt=0)
    target_accept: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def validate_parallelism(self) -> "SamplerConfig":
        if self.cores > self.chains:
            raise ValueError("cores cannot exceed chains")
        return self


class EvidenceGradingConfig(_StrictPolicyModel):
    min_effective_sample_size: float = Field(gt=0)
    min_symbols: int = Field(gt=0)
    min_days: int = Field(gt=0)
    weak_probability: float = Field(gt=0.5, lt=1)
    moderate_probability: float = Field(gt=0.5, lt=1)
    strong_probability: float = Field(gt=0.5, lt=1)
    very_strong_probability: float = Field(gt=0.5, lt=1)
    min_stable_windows: int = Field(ge=0)
    min_consistent_regimes: int = Field(ge=0)
    warning_grade_penalty: int = Field(ge=0)
    moderate_score: int = Field(ge=0)
    strong_score: int = Field(ge=0)
    very_strong_score: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_ordering(self) -> "EvidenceGradingConfig":
        probabilities = (
            self.weak_probability,
            self.moderate_probability,
            self.strong_probability,
            self.very_strong_probability,
        )
        if tuple(sorted(probabilities)) != probabilities or len(set(probabilities)) != 4:
            raise ValueError("evidence probabilities must be strictly increasing")
        scores = (self.moderate_score, self.strong_score, self.very_strong_score)
        if tuple(sorted(scores)) != scores or len(set(scores)) != 3:
            raise ValueError("evidence scores must be strictly increasing")
        return self


class TemporalSplitConfig(_StrictPolicyModel):
    discovery_fraction: float = Field(gt=0, lt=1)
    validation_fraction: float = Field(gt=0, lt=1)
    embargo_seconds: int = Field(ge=0)

    @model_validator(mode="after")
    def preserve_holdout(self) -> "TemporalSplitConfig":
        if self.discovery_fraction + self.validation_fraction >= 1:
            raise ValueError("split fractions must preserve a final holdout")
        return self


class ObjectiveWeights(_StrictPolicyModel):
    expectancy: float = Field(ge=0)
    profit_factor: float = Field(ge=0)
    stability: float = Field(ge=0)
    diversity: float = Field(ge=0)
    regime_consistency: float = Field(ge=0)
    sl_rate: float = Field(ge=0)
    drawdown: float = Field(ge=0)
    concentration: float = Field(ge=0)
    overfit: float = Field(ge=0)
    complexity: float = Field(ge=0)
    trial_volume: float = Field(ge=0)


class SearchDimensionConfig(_StrictPolicyModel):
    min: float
    max: float
    max_absolute_delta: float = Field(gt=0)
    step: float | None = Field(default=None, gt=0)
    type: Literal["int", "float"] = "float"

    @model_validator(mode="after")
    def validate_range(self) -> "SearchDimensionConfig":
        if self.min >= self.max:
            raise ValueError("search dimension min must be lower than max")
        return self


class BayesianPolicySchema(_StrictPolicyModel):
    policy_version: str = "custom"
    mode: Literal["analysis_only", "custom"] = "custom"
    max_trades: int = Field(gt=0)
    max_runtime_seconds: int = Field(gt=0)
    max_draws: int = Field(gt=0)
    max_tune: int = Field(gt=0)
    max_trials: int = Field(ge=0)
    max_workers: int = Field(gt=0)
    max_candidates: int = Field(ge=0)
    max_changes_per_candidate: int = Field(ge=0)
    min_trades: int = Field(gt=0)
    min_direct_samples: int = Field(gt=0)
    min_symbols: int = Field(gt=0)
    min_days: int = Field(gt=0)
    max_symbol_concentration: float = Field(gt=0, le=1)
    max_drawdown: float = Field(ge=0)
    min_expectancy_oos: float
    min_profit_factor: float = Field(ge=0)
    max_is_oos_degradation: float = Field(ge=0)
    min_regime_samples: int = Field(ge=0)
    credible_interval: float = Field(gt=0.5, lt=1)
    max_rhat: float = Field(ge=1)
    min_effective_sample_size: float = Field(gt=0)
    max_divergences: int = Field(ge=0)
    min_feature_coverage: float = Field(gt=0, le=1)
    sampler_config: SamplerConfig
    evidence_grading: EvidenceGradingConfig
    split_config: TemporalSplitConfig
    objective_weights: ObjectiveWeights
    authorized_search_space: dict[str, SearchDimensionConfig]
    permissions: dict[str, bool]

    @model_validator(mode="after")
    def validate_contract(self) -> "BayesianPolicySchema":
        missing_permissions = sorted(PERMISSION_KEYS - self.permissions.keys())
        unknown_permissions = sorted(self.permissions.keys() - PERMISSION_KEYS)
        if missing_permissions:
            raise ValueError(
                "permissions are incomplete: " + ", ".join(missing_permissions)
            )
        if unknown_permissions:
            raise ValueError(
                "permissions contain unknown keys: " + ", ".join(unknown_permissions)
            )
        if self.sampler_config.draws > self.max_draws:
            raise ValueError("sampler draws exceed max_draws")
        if self.sampler_config.tune > self.max_tune:
            raise ValueError("sampler tune exceeds max_tune")
        if self.sampler_config.cores > self.max_workers:
            raise ValueError("sampler cores exceed max_workers")
        return self


def _compact_validation_error(exc: ValidationError) -> str:
    items = []
    for error in exc.errors()[:12]:
        location = ".".join(str(part) for part in error["loc"])
        items.append(f"{location}: {error['msg']}")
    return "; ".join(items)


@dataclass(frozen=True)
class BayesianPolicy:
    values: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "BayesianPolicy":
        missing = sorted(REQUIRED_POLICY_KEYS - raw.keys())
        if missing:
            raise PolicyConfigurationError(
                "profile_bayesian policy is incomplete: " + ", ".join(missing)
            )
        if not isinstance(raw["authorized_search_space"], Mapping):
            raise PolicyConfigurationError("authorized_search_space must be an object")
        if not isinstance(raw["permissions"], Mapping):
            raise PolicyConfigurationError("permissions must be an object")
        try:
            validated = BayesianPolicySchema.model_validate(dict(raw))
        except ValidationError as exc:
            raise PolicyConfigurationError(
                "profile_bayesian policy is invalid: " + _compact_validation_error(exc)
            ) from exc
        return cls(validated.model_dump(mode="python"))

    def require_permission(self, permission: str) -> None:
        if self.values["permissions"].get(permission) is not True:
            raise PermissionError(f"permission denied: {permission}")

    def int(self, key: str) -> int:
        return int(self.values[key])

    def float(self, key: str) -> float:
        return float(self.values[key])


def require_analysis_only(policy: BayesianPolicy) -> None:
    values = policy.values
    if values.get("mode") != "analysis_only":
        raise PolicyConfigurationError("policy mode must be analysis_only")
    permissions = values["permissions"]
    if permissions.get("profile_bayesian.view") is not True:
        raise PolicyConfigurationError("analysis-only policy must allow view")
    if permissions.get("profile_bayesian.run_analysis") is not True:
        raise PolicyConfigurationError("analysis-only policy must allow run_analysis")
    enabled_mutations = sorted(
        permission
        for permission in MUTATING_PERMISSIONS
        if permissions.get(permission) is True
    )
    if enabled_mutations:
        raise PolicyConfigurationError(
            "analysis-only policy cannot enable: " + ", ".join(enabled_mutations)
        )
    if values["authorized_search_space"]:
        raise PolicyConfigurationError(
            "analysis-only policy must keep authorized_search_space empty"
        )
    if any(
        int(values[key]) != 0
        for key in ("max_trials", "max_candidates", "max_changes_per_candidate")
    ):
        raise PolicyConfigurationError(
            "analysis-only optimization and candidate limits must be zero"
        )


def load_analysis_only_policy_template() -> BayesianPolicy:
    path = (
        Path(__file__).resolve().parent
        / "policies"
        / f"{ANALYSIS_ONLY_TEMPLATE_ID}.json"
    )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyConfigurationError(
            "analysis-only policy template is unavailable"
        ) from exc
    policy = BayesianPolicy.from_mapping(raw)
    require_analysis_only(policy)
    return policy
