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
    prior_predictive_pnl_abs_limit_pct: float | None = None,
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
        max_tree_depth_hits = (
            int(sample_stats["reached_max_treedepth"].sum().values)
            if sample_stats is not None
            and "reached_max_treedepth" in sample_stats
            else 0
        )
        posterior_predictive = getattr(inference_data, "posterior_predictive", None)
        observed_data = getattr(inference_data, "observed_data", None)
        prior_predictive = getattr(inference_data, "prior_predictive", None)
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
            if variable == "outcome_observed":
                classes = sorted(
                    set(np.asarray(observed, dtype=int).reshape(-1).tolist())
                )
                class_checks: dict[str, Any] = {}
                class_passes: list[bool] = []
                for outcome_class in classes:
                    predictive_rate = (predictive == outcome_class).mean(axis=1)
                    observed_rate = float(
                        (np.asarray(observed).reshape(-1) == outcome_class).mean()
                    )
                    lower = float(np.quantile(predictive_rate, 0.025))
                    upper = float(np.quantile(predictive_rate, 0.975))
                    passed = lower <= observed_rate <= upper
                    class_passes.append(passed)
                    class_checks[str(outcome_class)] = {
                        "observed_rate": observed_rate,
                        "predictive_rate": float(predictive_rate.mean()),
                        "predictive_rate_interval_95": [lower, upper],
                        "passed": passed,
                    }
                ppc = {
                    "status": "PASS" if all(class_passes) else "WARNING",
                    "outcome_classes": class_checks,
                }
            else:
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
            prior_check: dict[str, Any] = {
                "status": "PRESENT" if prior_predictive is not None else "MISSING"
            }
            if (
                prior_predictive is not None
                and variable == "pnl_observed"
                and prior_predictive_pnl_abs_limit_pct is not None
            ):
                prior_values = np.asarray(
                    prior_predictive[variable], dtype=float
                )
                prior_abs_q995 = float(
                    np.quantile(np.abs(prior_values), 0.995)
                )
                prior_check.update(
                    {
                        "absolute_q995_pct": prior_abs_q995,
                        "absolute_limit_pct": (
                            prior_predictive_pnl_abs_limit_pct
                        ),
                        "status": (
                            "PASS"
                            if prior_abs_q995
                            <= prior_predictive_pnl_abs_limit_pct
                            else "FAIL"
                        ),
                    }
                )
            ppc["prior_predictive"] = prior_check
            if prior_predictive is None:
                warnings.append("prior_predictive_check_missing")
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
        elif (
            rhat_max > max_rhat
            or divergences > max_divergences
            or max_tree_depth_hits > 0
        ):
            status = DiagnosticStatus.NOT_CONVERGED
            warnings.append("convergence_gate_failed")
            if max_tree_depth_hits > 0:
                warnings.append("maximum_tree_depth_reached")
        elif ess_min < min_effective_sample_size:
            status = DiagnosticStatus.VALID_WITH_WARNINGS
            warnings.append("effective_sample_size_below_policy")
        if prior_predictive is None or ppc.get("prior_predictive", {}).get(
            "status"
        ) == "FAIL":
            status = DiagnosticStatus.NOT_CONVERGED
            warnings.append("prior_predictive_gate_failed")
        return DiagnosticResult(
            status=status,
            rhat_max=rhat_max,
            effective_sample_size_min=ess_min,
            divergences=divergences,
            posterior_predictive_check=ppc,
            credible_intervals={"probability": 0.95},
            warnings=tuple(warnings),
            details={
                "summary_rows": int(len(summary)),
                "maximum_tree_depth_hits": max_tree_depth_hits,
                "worst_rhat": [
                    {
                        "parameter": str(index),
                        "rhat": float(row["r_hat"]),
                    }
                    for index, row in summary.sort_values(
                        "r_hat", ascending=False
                    ).head(10).iterrows()
                    if math.isfinite(float(row["r_hat"]))
                ],
                "lowest_ess": [
                    {
                        "parameter": str(index),
                        "ess_bulk": float(row["ess_bulk"]),
                        "ess_tail": float(row["ess_tail"]),
                    }
                    for index, row in summary.assign(
                        ess_minimum=summary[["ess_bulk", "ess_tail"]].min(axis=1)
                    )
                    .sort_values("ess_minimum", ascending=True)
                    .head(10)
                    .iterrows()
                    if math.isfinite(float(row["ess_bulk"]))
                    and math.isfinite(float(row["ess_tail"]))
                ],
            },
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
