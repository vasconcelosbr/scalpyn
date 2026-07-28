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


class PriorPredictiveCheckFailed(ValueError):
    pass


def _require_plausible_pnl_prior(
    prior: Any, *, absolute_limit_pct: float
) -> None:
    values = np.asarray(prior.prior_predictive["pnl_observed"], dtype=float)
    absolute_quantile = float(np.quantile(np.abs(values), 0.995))
    if not np.isfinite(absolute_quantile) or absolute_quantile > absolute_limit_pct:
        raise PriorPredictiveCheckFailed(
            "prior_predictive_pnl_implausible:"
            f"abs_q995={absolute_quantile:.6g},"
            f"limit={absolute_limit_pct:.6g}"
        )


@dataclass(frozen=True)
class PreparedMatrix:
    x: np.ndarray
    tp: np.ndarray
    outcome: np.ndarray
    pnl: np.ndarray
    pnl_mask: np.ndarray
    feature_names: tuple[str, ...]
    dropped_features: tuple[str, ...]
    matrix_rank: int
    profile_index: np.ndarray
    symbol_index: np.ndarray
    regime_index: np.ndarray
    source_index: np.ndarray
    temporal_block_index: np.ndarray
    profile_labels: tuple[str, ...]
    symbol_labels: tuple[str, ...]
    regime_labels: tuple[str, ...]
    source_labels: tuple[str, ...]
    temporal_block_labels: tuple[str, ...]
    means: Mapping[str, float]
    scales: Mapping[str, float]
    coverage: Mapping[str, float]


OUTCOME_LABELS = ("SL_HIT", "TIMEOUT", "TP_HIT")
OUTCOME_INDEX = {label: index for index, label in enumerate(OUTCOME_LABELS)}


def _zero_sum_basis(size: int) -> np.ndarray:
    """Return a deterministic orthonormal Helmert basis for a zero-sum vector."""

    if size < 2:
        raise ValueError("zero-sum basis requires at least two levels")
    basis = np.zeros((size, size - 1), dtype=float)
    for column in range(size - 1):
        denominator = np.sqrt((column + 1) * (column + 2))
        basis[: column + 1, column] = 1.0 / denominator
        basis[column + 1, column] = -(column + 1) / denominator
    return basis


def _encode(values: Sequence[str]) -> tuple[np.ndarray, tuple[str, ...]]:
    labels = tuple(sorted(set(values)))
    index = {label: pos for pos, label in enumerate(labels)}
    return np.asarray([index[value] for value in values], dtype="int64"), labels


def prepare_matrix(
    observations: Sequence[CanonicalObservation],
    *,
    min_coverage: float,
    temporal_block_seconds: int | None = None,
    allowed_features: Sequence[str] | None = None,
) -> PreparedMatrix:
    if not observations:
        raise ValueError("observations cannot be empty")
    available_features = sorted(
        {name for item in observations for name in item.indicators.keys()}
    )
    allowed = set(allowed_features) if allowed_features is not None else None
    all_features = [
        name
        for name in available_features
        if allowed is None or name in allowed
    ]
    selected: list[str] = []
    dropped: list[str] = [
        name
        for name in available_features
        if allowed is not None and name not in allowed
    ]
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
        proposed = [(name, standardized, mean, scale)]
        if not valid.all():
            missing_name = f"{name}__missing"
            coverage[missing_name] = 1.0
            proposed.append((missing_name, (~valid).astype(float), 0.0, 1.0))
        for proposed_name, proposed_column, proposed_mean, proposed_scale in proposed:
            candidate = (
                proposed_column.reshape(-1, 1)
                if not columns
                else np.column_stack([*columns, proposed_column])
            )
            previous_rank = (
                0 if not columns else int(np.linalg.matrix_rank(np.column_stack(columns)))
            )
            candidate_rank = int(np.linalg.matrix_rank(candidate))
            if candidate_rank <= previous_rank:
                dropped.append(proposed_name)
                continue
            selected.append(proposed_name)
            means[proposed_name] = proposed_mean
            scales[proposed_name] = proposed_scale
            columns.append(proposed_column)
    if not columns:
        raise ValueError("no non-constant indicators meet the coverage policy")
    x = np.column_stack(columns)
    matrix_rank = int(np.linalg.matrix_rank(x))
    if matrix_rank != x.shape[1]:
        raise ValueError(
            "rank_deficient_indicator_matrix:"
            f"rank={matrix_rank},columns={x.shape[1]}"
        )
    profiles = [item.profile_id for item in observations]
    symbols = [item.symbol for item in observations]
    regimes = [item.regime or "UNKNOWN" for item in observations]
    sources = [item.source for item in observations]
    profile_index, profile_labels = _encode(profiles)
    symbol_index, symbol_labels = _encode(symbols)
    regime_index, regime_labels = _encode(regimes)
    source_index, source_labels = _encode(sources)
    if temporal_block_seconds is not None:
        if temporal_block_seconds <= 0:
            raise ValueError("temporal_block_seconds must be positive")
        temporal_blocks = [
            str(int(item.occurred_at.timestamp()) // temporal_block_seconds)
            for item in observations
        ]
        temporal_block_index, temporal_block_labels = _encode(temporal_blocks)
    else:
        temporal_block_index = np.zeros(len(observations), dtype="int64")
        temporal_block_labels = ()
    pnl = np.asarray(
        [np.nan if item.net_pnl_pct is None else item.net_pnl_pct for item in observations],
        dtype=float,
    )
    normalized_outcomes = [
        "TP_HIT"
        if item.outcome in {"TP", "TP_HIT"}
        else "SL_HIT"
        if item.outcome in {"SL", "SL_HIT"}
        else "TIMEOUT"
        for item in observations
    ]
    return PreparedMatrix(
        x=x,
        tp=np.asarray([item.tp_hit for item in observations], dtype="int8"),
        outcome=np.asarray(
            [OUTCOME_INDEX[label] for label in normalized_outcomes], dtype="int8"
        ),
        pnl=pnl,
        pnl_mask=np.isfinite(pnl),
        feature_names=tuple(selected),
        dropped_features=tuple(dropped),
        matrix_rank=matrix_rank,
        profile_index=profile_index,
        symbol_index=symbol_index,
        regime_index=regime_index,
        source_index=source_index,
        temporal_block_index=temporal_block_index,
        profile_labels=profile_labels,
        symbol_labels=symbol_labels,
        regime_labels=regime_labels,
        source_labels=source_labels,
        temporal_block_labels=temporal_block_labels,
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
    @staticmethod
    def _group_component(
        pm: Any,
        *,
        name: str,
        labels: Sequence[str],
        index: Any,
        dims: str,
        scale_sigma: float = 1.0,
    ) -> Any:
        """Return a non-centered random effect, or zero for a single level."""

        if len(labels) <= 1:
            return 0.0
        scale = pm.HalfNormal(f"{name}_scale", sigma=scale_sigma)
        offset = pm.Normal(f"{name}_offset", mu=0, sigma=1, dims=dims)
        effect = pm.Deterministic(f"{name}_effect", offset * scale, dims=dims)
        return effect[index]

    @staticmethod
    def _identified_group_component(
        pm: Any,
        *,
        name: str,
        labels: Sequence[str],
        index: Any,
        dims: str,
        prior_sigma: float,
    ) -> Any:
        """Return an exactly identified, regularized zero-sum group effect."""

        if len(labels) <= 1:
            return 0.0
        contrast = pm.Normal(
            f"{name}_contrast",
            mu=0,
            sigma=prior_sigma,
            shape=(len(labels) - 1,),
        )
        effect = pm.Deterministic(
            f"{name}_effect",
            pm.math.dot(_zero_sum_basis(len(labels)), contrast),
            dims=dims,
        )
        return effect[index]

    def fit_tp(self, matrix: PreparedMatrix, sampler: Mapping[str, Any]):
        pm = _load_pymc()
        coords = {
            "feature": matrix.feature_names,
            "profile": matrix.profile_labels,
            "symbol": matrix.symbol_labels,
            "regime": matrix.regime_labels,
            "source": matrix.source_labels,
            "observation": np.arange(matrix.x.shape[0]),
        }
        if matrix.temporal_block_labels:
            coords["temporal_block"] = matrix.temporal_block_labels
        with pm.Model(coords=coords) as model:
            x = pm.Data("x", matrix.x, dims=("observation", "feature"))
            p_idx = pm.Data("profile_idx", matrix.profile_index, dims="observation")
            s_idx = pm.Data("symbol_idx", matrix.symbol_index, dims="observation")
            r_idx = pm.Data("regime_idx", matrix.regime_index, dims="observation")
            source_idx = pm.Data(
                "source_idx", matrix.source_index, dims="observation"
            )
            b_idx = (
                pm.Data(
                    "temporal_block_idx",
                    matrix.temporal_block_index,
                    dims="observation",
                )
                if matrix.temporal_block_labels
                else None
            )
            intercept = pm.Normal("intercept_global", mu=0, sigma=1.5)
            profile_component = self._group_component(
                pm,
                name="profile",
                labels=matrix.profile_labels,
                index=p_idx,
                dims="profile",
            )
            symbol_component = self._group_component(
                pm,
                name="symbol",
                labels=matrix.symbol_labels,
                index=s_idx,
                dims="symbol",
            )
            regime_component = self._group_component(
                pm,
                name="regime",
                labels=matrix.regime_labels,
                index=r_idx,
                dims="regime",
            )
            source_component = self._group_component(
                pm,
                name="source",
                labels=matrix.source_labels,
                index=source_idx,
                dims="source",
            )
            temporal_component = self._group_component(
                pm,
                name="temporal_block",
                labels=matrix.temporal_block_labels,
                index=b_idx,
                dims="temporal_block",
            )
            beta = pm.Normal("indicator_effect", mu=0, sigma=1, dims="feature")
            eta = (
                intercept
                + profile_component
                + symbol_component
                + regime_component
                + source_component
                + temporal_component
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

    def fit_outcome(self, matrix: PreparedMatrix, sampler: Mapping[str, Any]):
        """Hierarchical TP/SL/TIMEOUT model with SL as the reference class."""

        pm = _load_pymc()
        outcome_logits = ("TIMEOUT", "TP_HIT")
        coords = {
            "feature": matrix.feature_names,
            "outcome_logit": outcome_logits,
            "profile": matrix.profile_labels,
            "symbol": matrix.symbol_labels,
            "regime": matrix.regime_labels,
            "source": matrix.source_labels,
            "observation": np.arange(matrix.x.shape[0]),
        }
        if matrix.temporal_block_labels:
            coords["temporal_block"] = matrix.temporal_block_labels
        with pm.Model(coords=coords) as model:
            x = pm.Data("x", matrix.x, dims=("observation", "feature"))
            p_idx = pm.Data("profile_idx", matrix.profile_index, dims="observation")
            s_idx = pm.Data("symbol_idx", matrix.symbol_index, dims="observation")
            r_idx = pm.Data("regime_idx", matrix.regime_index, dims="observation")
            source_idx = pm.Data(
                "source_idx", matrix.source_index, dims="observation"
            )
            b_idx = (
                pm.Data(
                    "temporal_block_idx",
                    matrix.temporal_block_index,
                    dims="observation",
                )
                if matrix.temporal_block_labels
                else None
            )
            intercept = pm.Normal(
                "outcome_intercept", mu=0, sigma=1.5, dims="outcome_logit"
            )
            profile_component = self._group_component(
                pm,
                name="profile",
                labels=matrix.profile_labels,
                index=p_idx,
                dims=("profile", "outcome_logit"),
            )
            symbol_component = self._group_component(
                pm,
                name="symbol",
                labels=matrix.symbol_labels,
                index=s_idx,
                dims=("symbol", "outcome_logit"),
            )
            regime_component = self._group_component(
                pm,
                name="regime",
                labels=matrix.regime_labels,
                index=r_idx,
                dims=("regime", "outcome_logit"),
            )
            source_component = self._group_component(
                pm,
                name="source",
                labels=matrix.source_labels,
                index=source_idx,
                dims=("source", "outcome_logit"),
            )
            temporal_component = self._group_component(
                pm,
                name="temporal_block",
                labels=matrix.temporal_block_labels,
                index=b_idx,
                dims=("temporal_block", "outcome_logit"),
            )
            beta = pm.Normal(
                "indicator_outcome_effect",
                mu=0,
                sigma=1,
                dims=("feature", "outcome_logit"),
            )
            non_reference_logits = (
                intercept
                + profile_component
                + symbol_component
                + regime_component
                + source_component
                + temporal_component
                + pm.math.dot(x, beta)
            )
            reference_logits = pm.math.zeros((matrix.x.shape[0], 1))
            logits = pm.math.concatenate(
                [reference_logits, non_reference_logits], axis=1
            )
            pm.Categorical(
                "outcome_observed",
                p=pm.math.softmax(logits, axis=1),
                observed=matrix.outcome,
                dims="observation",
            )
            prior = pm.sample_prior_predictive(
                samples=int(sampler["prior_predictive_samples"]),
                random_seed=int(sampler["random_seed"]),
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
            inference_data.extend(prior)
            pm.sample_posterior_predictive(
                inference_data,
                var_names=["outcome_observed"],
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
            "source": matrix.source_labels,
            "observation_pnl": np.arange(int(valid.sum())),
        }
        if matrix.temporal_block_labels:
            coords["temporal_block"] = matrix.temporal_block_labels
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
            source_idx = pm.Data(
                "source_idx", matrix.source_index[valid], dims="observation_pnl"
            )
            b_idx = (
                pm.Data(
                    "temporal_block_idx",
                    matrix.temporal_block_index[valid],
                    dims="observation_pnl",
                )
                if matrix.temporal_block_labels
                else None
            )
            intercept = pm.Normal("intercept_global", mu=0, sigma=2)
            profile_component = self._group_component(
                pm,
                name="profile",
                labels=matrix.profile_labels,
                index=p_idx,
                dims="profile",
            )
            symbol_component = self._group_component(
                pm,
                name="symbol",
                labels=matrix.symbol_labels,
                index=s_idx,
                dims="symbol",
            )
            regime_component = self._group_component(
                pm,
                name="regime",
                labels=matrix.regime_labels,
                index=r_idx,
                dims="regime",
            )
            source_component = self._group_component(
                pm,
                name="source",
                labels=matrix.source_labels,
                index=source_idx,
                dims="source",
            )
            temporal_component = self._group_component(
                pm,
                name="temporal_block",
                labels=matrix.temporal_block_labels,
                index=b_idx,
                dims="temporal_block",
            )
            beta = pm.Normal("indicator_effect", mu=0, sigma=1, dims="feature")
            residual_scale = pm.HalfNormal("residual_scale", sigma=2)
            nu_minus_two = pm.Exponential("nu_minus_two", lam=0.1)
            mu = (
                intercept
                + profile_component
                + symbol_component
                + regime_component
                + source_component
                + temporal_component
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

    def fit_conditional_pnl(
        self, matrix: PreparedMatrix, sampler: Mapping[str, Any]
    ):
        """Net-PnL model conditioned on TP/SL/TIMEOUT outcome."""

        pm = _load_pymc()
        valid = matrix.pnl_mask
        if not valid.any():
            raise ValueError("net PnL is unavailable for every observation")
        coords = {
            "feature": matrix.feature_names,
            "outcome": OUTCOME_LABELS,
            "observation_pnl": np.arange(int(valid.sum())),
        }
        if matrix.temporal_block_labels:
            coords["temporal_block"] = matrix.temporal_block_labels
        with pm.Model(coords=coords) as model:
            x = pm.Data("x", matrix.x[valid], dims=("observation_pnl", "feature"))
            outcome_idx = pm.Data(
                "outcome_idx", matrix.outcome[valid], dims="observation_pnl"
            )
            b_idx = (
                pm.Data(
                    "temporal_block_idx",
                    matrix.temporal_block_index[valid],
                    dims="observation_pnl",
                )
                if matrix.temporal_block_labels
                else None
            )
            # Outcome-specific intercepts are direct parameters. The previous
            # global + scale * offset decomposition was redundant and created
            # a funnel when an outcome (normally TIMEOUT) was sparse.
            outcome_intercept = pm.Normal(
                "pnl_outcome_intercept",
                mu=0,
                sigma=0.5,
                dims="outcome",
            )
            # Conditional barrier magnitude is not given independent
            # symbol/source/profile intercepts: those effects already enter
            # the multinomial outcome model. Keeping them here duplicated the
            # hierarchy and let them trade off against the PnL intercepts.
            # The temporal component remains to account for overlapping
            # market exposure, but is constrained to sum to zero so it cannot
            # absorb the intercept.
            temporal_component = self._identified_group_component(
                pm,
                name="temporal_block",
                labels=matrix.temporal_block_labels,
                index=b_idx,
                dims="temporal_block",
                prior_sigma=0.25,
            )
            # Direct regularized outcome coefficients remove the shared-scale
            # funnel from the previous global + scale * offset construction.
            beta = pm.Normal(
                "indicator_pnl_effect",
                mu=0,
                sigma=0.2,
                dims=("feature", "outcome"),
            )
            selected_beta = beta.T[outcome_idx]
            # A shared residual geometry keeps the sparse TIMEOUT class from
            # creating a weakly identified outcome-specific funnel.
            residual_scale = pm.HalfNormal("residual_scale", sigma=0.5)
            nu_minus_two = pm.Exponential("nu_minus_two", lam=0.1)
            mu = (
                outcome_intercept[outcome_idx]
                + temporal_component
                + pm.math.sum(x * selected_beta, axis=1)
            )
            pm.StudentT(
                "pnl_observed",
                nu=nu_minus_two + 2,
                mu=mu,
                sigma=residual_scale,
                observed=matrix.pnl[valid],
                dims="observation_pnl",
            )
            prior = pm.sample_prior_predictive(
                samples=int(sampler["prior_predictive_samples"]),
                random_seed=int(sampler["random_seed"]),
            )
            _require_plausible_pnl_prior(
                prior,
                absolute_limit_pct=float(
                    sampler["prior_predictive_pnl_abs_limit_pct"]
                ),
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
            inference_data.extend(prior)
            pm.sample_posterior_predictive(
                inference_data,
                var_names=["pnl_observed"],
                random_seed=int(sampler["random_seed"]),
                extend_inferencedata=True,
            )
            return inference_data
