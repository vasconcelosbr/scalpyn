"""ArviZ diagnostics with conservative status classification."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
from typing import Any, Mapping

import numpy as np

from .hierarchical_model import BayesianRuntimeUnavailable
from .schemas import DiagnosticStatus


@dataclass(frozen=True)
class DiagnosticResult:
    status: DiagnosticStatus
    rhat_max: float | None
    effective_sample_size_min: float | None
    divergences: int
    posterior_predictive_check: Mapping[str, Any]
    credible_intervals: Mapping[str, Any]
    warnings: tuple[str, ...]
    details: Mapping[str, Any]


def _load_arviz():
    try:
        return importlib.import_module("arviz")
    except Exception as exc:  # pragma: no cover - optional runtime
        raise BayesianRuntimeUnavailable(
            "ArviZ is unavailable in the dedicated analysis worker"
        ) from exc


def analyze_diagnostics(
    inference_data: Any,
    *,
    max_rhat: float,
    min_effective_sample_size: float,
    max_divergences: int,
) -> DiagnosticResult:
    az = _load_arviz()
    warnings: list[str] = []
    try:
        summary = az.summary(inference_data, kind="diagnostics")
        rhat_values = [
            float(value) for value in summary.get("r_hat", []) if math.isfinite(float(value))
        ]
        ess_values = [
            float(value)
            for name in ("ess_bulk", "ess_tail")
            for value in summary.get(name, [])
            if math.isfinite(float(value))
        ]
        rhat_max = max(rhat_values) if rhat_values else None
        ess_min = min(ess_values) if ess_values else None
        sample_stats = getattr(inference_data, "sample_stats", None)
        divergences = (
            int(sample_stats["diverging"].sum().values)
            if sample_stats is not None and "diverging" in sample_stats
            else 0
        )
        posterior_predictive = getattr(inference_data, "posterior_predictive", None)
        observed_data = getattr(inference_data, "observed_data", None)
        ppc: dict[str, Any]
        if posterior_predictive is None or observed_data is None:
            ppc = {"status": "MISSING"}
            status = DiagnosticStatus.NOT_CONVERGED
            warnings.append("posterior_predictive_check_missing")
        else:
            variable = next(iter(posterior_predictive.data_vars))
            observed_variable = next(iter(observed_data.data_vars))
            predictive = np.asarray(posterior_predictive[variable]).reshape(
                -1, posterior_predictive[variable].shape[-1]
            )
            observed = np.asarray(observed_data[observed_variable])
            predictive_means = predictive.mean(axis=1)
            observed_mean = float(observed.mean())
            lower = float(np.quantile(predictive_means, 0.025))
            upper = float(np.quantile(predictive_means, 0.975))
            ppc = {
                "status": "PASS" if lower <= observed_mean <= upper else "WARNING",
                "observed_mean": observed_mean,
                "predictive_mean": float(predictive_means.mean()),
                "predictive_mean_interval_95": [lower, upper],
            }
            status = (
                DiagnosticStatus.VALID
                if ppc["status"] == "PASS"
                else DiagnosticStatus.VALID_WITH_WARNINGS
            )
            if ppc["status"] != "PASS":
                warnings.append("posterior_predictive_check_warning")
        if rhat_max is None or ess_min is None:
            status = DiagnosticStatus.INSUFFICIENT_EVIDENCE
            warnings.append("diagnostics_missing")
        elif rhat_max > max_rhat or divergences > max_divergences:
            status = DiagnosticStatus.NOT_CONVERGED
            warnings.append("convergence_gate_failed")
        elif ess_min < min_effective_sample_size:
            status = DiagnosticStatus.VALID_WITH_WARNINGS
            warnings.append("effective_sample_size_below_policy")
        return DiagnosticResult(
            status=status,
            rhat_max=rhat_max,
            effective_sample_size_min=ess_min,
            divergences=divergences,
            posterior_predictive_check=ppc,
            credible_intervals={"probability": 0.95},
            warnings=tuple(warnings),
            details={"summary_rows": int(len(summary))},
        )
    except Exception as exc:
        return DiagnosticResult(
            status=DiagnosticStatus.FAILED,
            rhat_max=None,
            effective_sample_size_min=None,
            divergences=0,
            posterior_predictive_check={"status": "FAILED"},
            credible_intervals={},
            warnings=("diagnostic_exception",),
            details={"error": str(exc)},
        )
