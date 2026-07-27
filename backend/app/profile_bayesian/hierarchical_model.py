"""Lazy-loaded hierarchical TP and net-PnL models.

Importing this module does not import PyMC or ArviZ. The optional scientific
runtime is loaded only inside the dedicated analysis worker.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Any, Mapping, Sequence

import numpy as np

from .data_contract import CanonicalObservation


class BayesianRuntimeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedMatrix:
    x: np.ndarray
    tp: np.ndarray
    pnl: np.ndarray
    pnl_mask: np.ndarray
    feature_names: tuple[str, ...]
    profile_index: np.ndarray
    symbol_index: np.ndarray
    regime_index: np.ndarray
    profile_labels: tuple[str, ...]
    symbol_labels: tuple[str, ...]
    regime_labels: tuple[str, ...]
    means: Mapping[str, float]
    scales: Mapping[str, float]
    coverage: Mapping[str, float]


def _encode(values: Sequence[str]) -> tuple[np.ndarray, tuple[str, ...]]:
    labels = tuple(sorted(set(values)))
    index = {label: pos for pos, label in enumerate(labels)}
    return np.asarray([index[value] for value in values], dtype="int64"), labels


def prepare_matrix(
    observations: Sequence[CanonicalObservation],
    *,
    min_coverage: float,
) -> PreparedMatrix:
    if not observations:
        raise ValueError("observations cannot be empty")
    all_features = sorted(
        {name for item in observations for name in item.indicators.keys()}
    )
    selected: list[str] = []
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    coverage: dict[str, float] = {}
    columns: list[np.ndarray] = []
    for name in all_features:
        raw = np.asarray(
            [
                np.nan if item.indicators.get(name) is None else item.indicators[name]
                for item in observations
            ],
            dtype=float,
        )
        valid = np.isfinite(raw)
        coverage[name] = float(valid.mean())
        if coverage[name] < min_coverage or not valid.any():
            continue
        mean = float(np.nanmean(raw))
        scale = float(np.nanstd(raw))
        if not np.isfinite(scale) or scale == 0:
            continue
        # Missingness is explicit: standardized values are centered, and a
        # missingness indicator is added as a separate feature.
        standardized = np.where(valid, (raw - mean) / scale, 0.0)
        selected.append(name)
        means[name] = mean
        scales[name] = scale
        columns.append(standardized)
        if not valid.all():
            missing_name = f"{name}__missing"
            selected.append(missing_name)
            means[missing_name] = 0.0
            scales[missing_name] = 1.0
            coverage[missing_name] = 1.0
            columns.append((~valid).astype(float))
    if not columns:
        raise ValueError("no non-constant indicators meet the coverage policy")
    profiles = [item.profile_id for item in observations]
    symbols = [item.symbol for item in observations]
    regimes = [item.regime or "UNKNOWN" for item in observations]
    profile_index, profile_labels = _encode(profiles)
    symbol_index, symbol_labels = _encode(symbols)
    regime_index, regime_labels = _encode(regimes)
    pnl = np.asarray(
        [np.nan if item.net_pnl_pct is None else item.net_pnl_pct for item in observations],
        dtype=float,
    )
    return PreparedMatrix(
        x=np.column_stack(columns),
        tp=np.asarray([item.tp_hit for item in observations], dtype="int8"),
        pnl=pnl,
        pnl_mask=np.isfinite(pnl),
        feature_names=tuple(selected),
        profile_index=profile_index,
        symbol_index=symbol_index,
        regime_index=regime_index,
        profile_labels=profile_labels,
        symbol_labels=symbol_labels,
        regime_labels=regime_labels,
        means=means,
        scales=scales,
        coverage=coverage,
    )


def _load_pymc():
    try:
        return importlib.import_module("pymc")
    except Exception as exc:  # pragma: no cover - depends on optional runtime
        raise BayesianRuntimeUnavailable(
            "PyMC is unavailable; install requirements-profile-bayesian.txt "
            "only in the dedicated analysis worker"
        ) from exc


class HierarchicalModel:
    def fit_tp(self, matrix: PreparedMatrix, sampler: Mapping[str, Any]):
        pm = _load_pymc()
        coords = {
            "feature": matrix.feature_names,
            "profile": matrix.profile_labels,
            "symbol": matrix.symbol_labels,
            "regime": matrix.regime_labels,
            "observation": np.arange(matrix.x.shape[0]),
        }
        with pm.Model(coords=coords) as model:
            x = pm.Data("x", matrix.x, dims=("observation", "feature"))
            p_idx = pm.Data("profile_idx", matrix.profile_index, dims="observation")
            s_idx = pm.Data("symbol_idx", matrix.symbol_index, dims="observation")
            r_idx = pm.Data("regime_idx", matrix.regime_index, dims="observation")
            intercept = pm.Normal("intercept_global", mu=0, sigma=1.5)
            profile_scale = pm.HalfNormal("profile_scale", sigma=1)
            symbol_scale = pm.HalfNormal("symbol_scale", sigma=1)
            regime_scale = pm.HalfNormal("regime_scale", sigma=1)
            profile_effect = pm.Normal(
                "profile_effect", mu=0, sigma=profile_scale, dims="profile"
            )
            symbol_effect = pm.Normal(
                "symbol_effect", mu=0, sigma=symbol_scale, dims="symbol"
            )
            regime_effect = pm.Normal(
                "regime_effect", mu=0, sigma=regime_scale, dims="regime"
            )
            beta = pm.Normal("indicator_effect", mu=0, sigma=1, dims="feature")
            eta = (
                intercept
                + profile_effect[p_idx]
                + symbol_effect[s_idx]
                + regime_effect[r_idx]
                + pm.math.dot(x, beta)
            )
            pm.Bernoulli("tp_observed", logit_p=eta, observed=matrix.tp, dims="observation")
            inference_data = pm.sample(
                draws=int(sampler["draws"]),
                tune=int(sampler["tune"]),
                chains=int(sampler["chains"]),
                cores=int(sampler["cores"]),
                random_seed=int(sampler["random_seed"]),
                target_accept=float(sampler["target_accept"]),
                return_inferencedata=True,
                idata_kwargs={"log_likelihood": True},
            )
            pm.sample_posterior_predictive(
                inference_data,
                var_names=["tp_observed"],
                random_seed=int(sampler["random_seed"]),
                extend_inferencedata=True,
            )
            return inference_data

    def fit_pnl(self, matrix: PreparedMatrix, sampler: Mapping[str, Any]):
        pm = _load_pymc()
        valid = matrix.pnl_mask
        if not valid.any():
            raise ValueError("net PnL is unavailable for every observation")
        coords = {
            "feature": matrix.feature_names,
            "profile": matrix.profile_labels,
            "symbol": matrix.symbol_labels,
            "regime": matrix.regime_labels,
            "observation_pnl": np.arange(int(valid.sum())),
        }
        with pm.Model(coords=coords) as model:
            x = pm.Data("x", matrix.x[valid], dims=("observation_pnl", "feature"))
            p_idx = pm.Data(
                "profile_idx", matrix.profile_index[valid], dims="observation_pnl"
            )
            s_idx = pm.Data(
                "symbol_idx", matrix.symbol_index[valid], dims="observation_pnl"
            )
            r_idx = pm.Data(
                "regime_idx", matrix.regime_index[valid], dims="observation_pnl"
            )
            intercept = pm.Normal("intercept_global", mu=0, sigma=2)
            profile_scale = pm.HalfNormal("profile_scale", sigma=1)
            symbol_scale = pm.HalfNormal("symbol_scale", sigma=1)
            regime_scale = pm.HalfNormal("regime_scale", sigma=1)
            profile_effect = pm.Normal(
                "profile_effect", mu=0, sigma=profile_scale, dims="profile"
            )
            symbol_effect = pm.Normal(
                "symbol_effect", mu=0, sigma=symbol_scale, dims="symbol"
            )
            regime_effect = pm.Normal(
                "regime_effect", mu=0, sigma=regime_scale, dims="regime"
            )
            beta = pm.Normal("indicator_effect", mu=0, sigma=1, dims="feature")
            residual_scale = pm.HalfNormal("residual_scale", sigma=2)
            nu_minus_two = pm.Exponential("nu_minus_two", lam=0.1)
            mu = (
                intercept
                + profile_effect[p_idx]
                + symbol_effect[s_idx]
                + regime_effect[r_idx]
                + pm.math.dot(x, beta)
            )
            pm.StudentT(
                "pnl_observed",
                nu=nu_minus_two + 2,
                mu=mu,
                sigma=residual_scale,
                observed=matrix.pnl[valid],
                dims="observation_pnl",
            )
            inference_data = pm.sample(
                draws=int(sampler["draws"]),
                tune=int(sampler["tune"]),
                chains=int(sampler["chains"]),
                cores=int(sampler["cores"]),
                random_seed=int(sampler["random_seed"]),
                target_accept=float(sampler["target_accept"]),
                return_inferencedata=True,
                idata_kwargs={"log_likelihood": True},
            )
            pm.sample_posterior_predictive(
                inference_data,
                var_names=["pnl_observed"],
                random_seed=int(sampler["random_seed"]),
                extend_inferencedata=True,
            )
            return inference_data
