"""Fail-closed flags, policy loading, and operational authority."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping


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


class PolicyConfigurationError(ValueError):
    pass


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
        return cls(dict(raw))

    def require_permission(self, permission: str) -> None:
        if self.values["permissions"].get(permission) is not True:
            raise PermissionError(f"permission denied: {permission}")

    def int(self, key: str) -> int:
        return int(self.values[key])

    def float(self, key: str) -> float:
        return float(self.values[key])
