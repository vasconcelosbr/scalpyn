"""Low-cardinality Prometheus metrics with graceful degradation."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Histogram
except Exception:  # pragma: no cover - optional observability dependency
    Counter = Histogram = None  # type: ignore[assignment]


def _counter(name: str, description: str):
    if Counter is None:
        return None
    try:
        return Counter(name, description)
    except ValueError:
        return None


def _histogram(name: str, description: str):
    if Histogram is None:
        return None
    try:
        return Histogram(name, description)
    except ValueError:
        return None


ANALYSIS_TOTAL = _counter("bayesian_analysis_total", "Bayesian analyses requested.")
ANALYSIS_SUCCESS = _counter(
    "bayesian_analysis_success_total", "Bayesian analyses completed."
)
ANALYSIS_FAILED = _counter(
    "bayesian_analysis_failed_total", "Bayesian analyses failed."
)
ANALYSIS_DURATION = _histogram(
    "bayesian_analysis_duration_seconds", "End-to-end Bayesian analysis duration."
)
SAMPLING_DURATION = _histogram(
    "bayesian_sampling_duration_seconds", "Bayesian posterior sampling duration."
)
DIVERGENCES = _counter(
    "bayesian_divergences_total", "Divergences observed in posterior sampling."
)
NON_CONVERGED = _counter(
    "bayesian_non_converged_total", "Analyses rejected for non-convergence."
)
OPTIMIZATION_STUDIES = _counter(
    "optimization_studies_total", "Optimization studies requested."
)
OPTIMIZATION_TRIALS = _counter(
    "optimization_trials_total", "Optimization trials completed."
)
OPTIMIZATION_VALID_TRIALS = _counter(
    "optimization_valid_trials_total", "Optimization trials passing all constraints."
)
OPTIMIZATION_FAILED_TRIALS = _counter(
    "optimization_failed_trials_total", "Optimization trials failing or rejected."
)
CANDIDATES_GENERATED = _counter(
    "candidates_generated_total", "Bayesian candidate drafts created."
)
CANDIDATES_REJECTED = _counter(
    "candidates_rejected_total", "Bayesian candidates rejected."
)


def increment(metric: Any, amount: float = 1.0) -> None:
    if metric is not None:
        metric.inc(amount)
