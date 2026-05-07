"""Indicator Validator — Integrity checks for indicator envelopes.

This module implements validation rules to ensure indicator data integrity
and prevent invalid scoring scenarios.
"""

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .indicator_envelope import IndicatorEnvelope, IndicatorStatus, DataSource

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """Validation error details."""
    rule_name: str
    indicator_name: Optional[str]
    message: str
    severity: str  # "CRITICAL" | "WARNING"


@dataclass
class ValidationResult:
    """Result of indicator validation."""
    valid: bool
    errors: List[ValidationError]
    warnings: List[str]


@dataclass
class ValidationRule:
    """A validation rule for indicator integrity."""
    name: str
    check: Callable[[Dict[str, IndicatorEnvelope]], bool]
    severity: str  # "CRITICAL" | "WARNING"
    error_message: str
    get_affected_indicators: Callable[[Dict[str, IndicatorEnvelope]], List[str]] = lambda envs: []


# Critical indicators that must be available for trading
CRITICAL_INDICATORS = [
    "volume_24h_usdt",
    "rsi",
    "adx",
]


def check_volume_delta_exclusivity(envelopes: Dict[str, IndicatorEnvelope]) -> bool:
    """Ensure only one volume_delta bucket can be PASS at a time."""
    bucket_indicators = [
        k for k in envelopes.keys()
        if k.startswith("volume_delta_") and k != "volume_delta"
    ]

    passing_buckets = [
        k for k in bucket_indicators
        if envelopes[k].status == IndicatorStatus.PASS
    ]

    return len(passing_buckets) <= 1


def check_critical_indicators_available(envelopes: Dict[str, IndicatorEnvelope]) -> bool:
    """Ensure critical indicators have data."""
    for ind_name in CRITICAL_INDICATORS:
        if ind_name not in envelopes:
            logger.error(f"Critical indicator missing: {ind_name}")
            return False

        envelope = envelopes[ind_name]
        if envelope.status == IndicatorStatus.NO_DATA:
            logger.error(f"Critical indicator has NO_DATA: {ind_name}")
            return False

        if not envelope.valid:
            logger.error(f"Critical indicator is invalid: {ind_name}")
            return False

    return True


def check_flow_indicators_primary_source(envelopes: Dict[str, IndicatorEnvelope]) -> bool:
    """Warn if flow indicators use candle fallback with high confidence."""
    flow_indicators = ["taker_ratio", "volume_delta"]

    for ind_name in flow_indicators:
        if ind_name not in envelopes:
            continue

        envelope = envelopes[ind_name]
        if envelope.source == DataSource.CANDLE_APPROX and envelope.confidence > 0.6:
            logger.warning(
                f"Flow indicator {ind_name} using candle fallback with high confidence: "
                f"{envelope.confidence:.2f}"
            )
            return False

    return True


def check_derived_indicators_dependencies(envelopes: Dict[str, IndicatorEnvelope]) -> bool:
    """Ensure derived indicators have valid dependencies."""
    for env in envelopes.values():
        if env.source != DataSource.DERIVED:
            continue

        for dep in env.dependencies:
            if dep not in envelopes:
                logger.error(
                    f"Derived indicator {env.name} missing dependency: {dep}"
                )
                return False

            dep_env = envelopes[dep]
            if not dep_env.valid:
                logger.error(
                    f"Derived indicator {env.name} has invalid dependency: {dep} "
                    f"(status={dep_env.status.value})"
                )
                return False

    return True


def check_sufficient_candles(envelopes: Dict[str, IndicatorEnvelope]) -> bool:
    """Ensure indicators have sufficient candles for calculation."""
    for env in envelopes.values():
        if env.min_candles_required is None:
            continue

        if env.actual_candles is None:
            logger.warning(
                f"Indicator {env.name} requires {env.min_candles_required} candles "
                "but actual_candles is None"
            )
            continue

        if env.actual_candles < env.min_candles_required:
            logger.error(
                f"Indicator {env.name} has insufficient candles: "
                f"need {env.min_candles_required}, have {env.actual_candles}"
            )
            return False

    return True


def check_no_stale_critical_indicators(envelopes: Dict[str, IndicatorEnvelope]) -> bool:
    """Ensure critical indicators are not stale."""
    for ind_name in CRITICAL_INDICATORS:
        if ind_name not in envelopes:
            continue

        envelope = envelopes[ind_name]
        if envelope.status == IndicatorStatus.STALE:
            logger.error(f"Critical indicator is STALE: {ind_name}")
            return False

    return True


def check_minimum_confidence(envelopes: Dict[str, IndicatorEnvelope], min_confidence: float = 0.5) -> bool:
    """Ensure critical indicators meet minimum confidence."""
    for ind_name in CRITICAL_INDICATORS:
        if ind_name not in envelopes:
            continue

        envelope = envelopes[ind_name]
        if envelope.confidence < min_confidence:
            logger.error(
                f"Critical indicator {ind_name} has low confidence: "
                f"{envelope.confidence:.2f} < {min_confidence}"
            )
            return False

    return True


# Validation rules registry
VALIDATION_RULES = [
    ValidationRule(
        name="volume_delta_bucket_exclusivity",
        check=check_volume_delta_exclusivity,
        severity="CRITICAL",
        error_message="Multiple volume_delta buckets are PASS simultaneously",
    ),
    ValidationRule(
        name="critical_indicators_available",
        check=check_critical_indicators_available,
        severity="CRITICAL",
        error_message="Critical indicators have NO_DATA or are invalid",
    ),
    ValidationRule(
        name="flow_indicators_primary_source",
        check=check_flow_indicators_primary_source,
        severity="WARNING",
        error_message="Flow indicators using candle fallback with high confidence",
    ),
    ValidationRule(
        name="derived_indicators_dependencies",
        check=check_derived_indicators_dependencies,
        severity="CRITICAL",
        error_message="Derived indicators have invalid dependencies",
    ),
    ValidationRule(
        name="sufficient_candles_for_calculation",
        check=check_sufficient_candles,
        severity="CRITICAL",
        error_message="Insufficient candles for indicator calculation",
    ),
    ValidationRule(
        name="no_stale_critical_indicators",
        check=check_no_stale_critical_indicators,
        severity="CRITICAL",
        error_message="Critical indicators are STALE",
    ),
    ValidationRule(
        name="minimum_confidence_critical",
        check=check_minimum_confidence,
        severity="CRITICAL",
        error_message="Critical indicators have confidence below minimum threshold",
    ),
]


def validate_indicator_integrity(
    envelopes: Dict[str, IndicatorEnvelope]
) -> ValidationResult:
    """Execute all validation rules on indicator envelopes.

    Args:
        envelopes: Dictionary of indicator name -> IndicatorEnvelope

    Returns:
        ValidationResult with errors and warnings
    """
    errors = []
    warnings = []

    for rule in VALIDATION_RULES:
        try:
            if not rule.check(envelopes):
                error = ValidationError(
                    rule_name=rule.name,
                    indicator_name=None,
                    message=rule.error_message,
                    severity=rule.severity,
                )

                if rule.severity == "CRITICAL":
                    errors.append(error)
                else:
                    warnings.append(rule.error_message)

        except Exception as e:
            logger.exception(f"Validation rule {rule.name} failed with exception")
            errors.append(
                ValidationError(
                    rule_name=rule.name,
                    indicator_name=None,
                    message=f"Validation rule failed: {e}",
                    severity="CRITICAL",
                )
            )

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def get_validation_summary(result: ValidationResult) -> str:
    """Get human-readable summary of validation result."""
    if result.valid:
        summary = "✓ All validation checks passed"
        if result.warnings:
            summary += f" ({len(result.warnings)} warnings)"
        return summary

    summary = f"✗ Validation failed with {len(result.errors)} error(s)"
    if result.warnings:
        summary += f" and {len(result.warnings)} warning(s)"

    for error in result.errors:
        summary += f"\n  - {error.rule_name}: {error.message}"

    for warning in result.warnings:
        summary += f"\n  ⚠ {warning}"

    return summary
