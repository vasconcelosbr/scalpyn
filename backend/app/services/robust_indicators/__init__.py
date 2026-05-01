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
from .bucketing import (
    bucketed_symbols,
    get_rollout_percent,
    should_use_robust,
)
from .select_score import (
    SelectScoreResult,
    compute_robust_score,
    select_authoritative_score,
)
from .preflight import (
    PreflightResult,
    check_safe_to_raise,
    collect_window_metrics,
)

__all__ = [
    "CONFIDENCE_MAP",
    "STALENESS_PENALTY",
    "DataSource",
    "IndicatorEnvelope",
    "IndicatorStatus",
    "PreflightResult",
    "ScoreResult",
    "SelectScoreResult",
    "ValidationResult",
    "ValidationRule",
    "bucketed_symbols",
    "calculate_score_with_confidence",
    "check_safe_to_raise",
    "collect_window_metrics",
    "compute_indicators_robust",
    "compute_robust_score",
    "get_rollout_percent",
    "is_shadow_enabled",
    "persist_snapshot",
    "run_shadow_scan",
    "select_authoritative_score",
    "should_use_robust",
    "validate_indicator_integrity",
    "wrap_indicator",
]
