"""Robust Indicators — Phase 1 (shadow mode).

This package implements the new indicator architecture in parallel with the
legacy ``feature_engine`` / ``score_engine`` path. Phase 1 only reads, computes,
persists snapshots and logs divergence; legacy scoring stays authoritative.

Public surface (re-exported here for convenience):
    * IndicatorEnvelope, ValidationRule, ValidationResult, ScoreResult
    * IndicatorStatus, DataSource
    * CONFIDENCE_MAP, STALENESS_PENALTY
    * wrap_indicator
    * validate_indicator_integrity
    * calculate_score_with_confidence
    * compute_indicators_robust
    * persist_snapshot
    * run_shadow_scan
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
from .compute import compute_indicators_robust
from .snapshot import persist_snapshot
from .shadow import is_shadow_enabled, run_shadow_scan

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
    "compute_indicators_robust",
    "is_shadow_enabled",
    "persist_snapshot",
    "run_shadow_scan",
    "validate_indicator_integrity",
    "wrap_indicator",
]
