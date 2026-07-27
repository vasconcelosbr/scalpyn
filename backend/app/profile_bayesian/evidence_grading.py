"""Evidence grading separated from model fitting for auditability."""

from __future__ import annotations

from typing import Mapping

from .schemas import DiagnosticStatus, EvidenceGrade


def grade_evidence(
    *,
    probability_positive: float | None,
    credible_interval: tuple[float | None, float | None],
    effective_sample_size: float | None,
    symbol_count: int,
    day_count: int,
    stable_windows: int,
    consistent_regimes: int,
    diagnostic_status: DiagnosticStatus,
    policy: Mapping[str, float | int],
) -> EvidenceGrade:
    if diagnostic_status not in {
        DiagnosticStatus.VALID,
        DiagnosticStatus.VALID_WITH_WARNINGS,
    }:
        return EvidenceGrade.INSUFFICIENT
    if probability_positive is None or effective_sample_size is None:
        return EvidenceGrade.INSUFFICIENT
    lower, upper = credible_interval
    if lower is None or upper is None:
        return EvidenceGrade.INSUFFICIENT
    if (
        effective_sample_size < float(policy["min_effective_sample_size"])
        or symbol_count < int(policy["min_symbols"])
        or day_count < int(policy["min_days"])
    ):
        return EvidenceGrade.INSUFFICIENT
    confidence = max(probability_positive, 1.0 - probability_positive)
    score = 0
    score += int(confidence >= float(policy["weak_probability"]))
    score += int(confidence >= float(policy["moderate_probability"]))
    score += int(confidence >= float(policy["strong_probability"]))
    score += int(confidence >= float(policy["very_strong_probability"]))
    score += int(lower > 0 or upper < 0)
    score += int(stable_windows >= int(policy["min_stable_windows"]))
    score += int(consistent_regimes >= int(policy["min_consistent_regimes"]))
    if diagnostic_status == DiagnosticStatus.VALID_WITH_WARNINGS:
        score = max(0, score - int(policy["warning_grade_penalty"]))
    if score >= int(policy["very_strong_score"]):
        return EvidenceGrade.VERY_STRONG
    if score >= int(policy["strong_score"]):
        return EvidenceGrade.STRONG
    if score >= int(policy["moderate_score"]):
        return EvidenceGrade.MODERATE
    return EvidenceGrade.WEAK
