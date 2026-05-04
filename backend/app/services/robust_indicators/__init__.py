"""Robust Indicators package — deterministic scoring path (Task #211).

Scoring is deterministic: matched rules award their full configured points
(no confidence weighting). Confidence is tracked per rule for observability
and ``can_trade`` gating but does NOT multiply into the score.

Public surface (re-exported here for convenience):
    * IndicatorEnvelope, ValidationRule, ValidationResult, ScoreResult
    * IndicatorStatus, DataSource
    * CONFIDENCE_MAP, STALENESS_PENALTY
    * wrap_indicator
    * validate_indicator_integrity
    * calculate_score_with_confidence
    * compute_indicators_robust
    * envelope_indicators
    * persist_snapshot
"""

from .envelope import (
    CONFIDENCE_MAP,
    STALENESS_PENALTY,
    DataSource,
    IndicatorEnvelope,
    IndicatorStatus,
    wrap_indicator,
)
from .validation import (
    ValidationResult,
    ValidationRule,
    validate_indicator_integrity,
)
from .score import ScoreResult, calculate_score_with_confidence
from .compute import compute_indicators_robust, envelope_indicators
from .snapshot import persist_snapshot
from .asset_score import compute_asset_score, robust_futures_direction_bias

__all__ = [
    "CONFIDENCE_MAP",
    "STALENESS_PENALTY",
    "DataSource",
    "IndicatorEnvelope",
    "IndicatorStatus",
    "ScoreResult",
    "ValidationResult",
    "ValidationRule",
    "calculate_score_with_confidence",
    "compute_asset_score",
    "compute_indicators_robust",
    "envelope_indicators",
    "persist_snapshot",
    "robust_futures_direction_bias",
    "validate_indicator_integrity",
    "wrap_indicator",
]
