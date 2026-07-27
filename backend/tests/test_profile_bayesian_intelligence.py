from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from app.api import profile_bayesian_intelligence as bayesian_api
from app.profile_bayesian.config import (
    BayesianPolicy,
    PolicyConfigurationError,
    feature_flags,
    load_analysis_only_policy_template,
    require_analysis_only,
)
from app.profile_bayesian.data_contract import (
    CanonicalObservation,
    canonical_hash,
    extract_indicators,
    finite_number,
)
from app.profile_bayesian.dataset_builder import BayesianDatasetBuilder
from app.profile_bayesian.evidence_grading import grade_evidence
from app.profile_bayesian.hierarchical_model import prepare_matrix
from app.profile_bayesian.optimization.constraints import constraint_violations
from app.profile_bayesian.optimization.objective import robust_score
from app.profile_bayesian.optimization.search_space import build_search_space
from app.profile_bayesian.schemas import DiagnosticStatus, EvidenceGrade
from app.profile_bayesian.validation.profile_replay_adapter import ProfileReplayAdapter
from app.profile_bayesian.validation.temporal_split import purged_temporal_split


def _observation(
    index: int,
    *,
    rsi: float | None,
    constant: float = 1.0,
) -> CanonicalObservation:
    return CanonicalObservation(
        observation_id=f"event-{index}",
        profile_id="profile-a",
        profile_version_id="version-a",
        symbol="BTC_USDT" if index % 2 == 0 else "ETH_USDT",
        timeframe="5m",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        + timedelta(days=index),
        outcome="TP_HIT" if index % 2 == 0 else "SL_HIT",
        tp_hit=int(index % 2 == 0),
        net_pnl_pct=1.0 if index % 2 == 0 else -1.0,
        regime="TREND" if index % 2 == 0 else "RANGE",
        policy_key="policy-a",
        indicators={"rsi": rsi, "constant": constant},
        source="L3_LAB",
    )


def test_flags_default_false_and_auto_promotion_cannot_be_enabled(monkeypatch):
    names = (
        "PROFILE_BAYESIAN_ENABLED",
        "PROFILE_BAYESIAN_ANALYSIS_ENABLED",
        "PROFILE_BAYESIAN_OPTIMIZATION_ENABLED",
        "PROFILE_BAYESIAN_CANDIDATE_CREATION_ENABLED",
        "PROFILE_BAYESIAN_SHADOW_SUBMISSION_ENABLED",
        "PROFILE_BAYESIAN_AUTO_PROMOTION_ENABLED",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert all(value is False for value in feature_flags().__dict__.values())
    monkeypatch.setenv("PROFILE_BAYESIAN_AUTO_PROMOTION_ENABLED", "true")
    assert feature_flags().auto_promotion_enabled is False


def test_policy_is_fail_closed_when_any_required_key_is_missing():
    with pytest.raises(PolicyConfigurationError, match="policy is incomplete"):
        BayesianPolicy.from_mapping({})


def test_versioned_analysis_only_policy_is_complete_and_blocks_mutations():
    policy = load_analysis_only_policy_template()

    require_analysis_only(policy)
    assert policy.values["policy_version"] == "analysis_only_v1"
    assert policy.values["permissions"]["profile_bayesian.run_analysis"] is True
    assert policy.values["permissions"]["profile_bayesian.run_optimization"] is False
    assert policy.values["authorized_search_space"] == {}
    assert policy.values["max_trials"] == 0
    assert policy.values["max_candidates"] == 0


def test_analysis_only_policy_rejects_unsafe_import():
    policy = load_analysis_only_policy_template()
    raw = dict(policy.values)
    raw["permissions"] = {
        **raw["permissions"],
        "profile_bayesian.create_candidate": True,
    }

    with pytest.raises(PolicyConfigurationError, match="cannot enable"):
        require_analysis_only(BayesianPolicy.from_mapping(raw))


def test_policy_deep_validation_rejects_invalid_sampler():
    policy = load_analysis_only_policy_template()
    raw = dict(policy.values)
    raw["sampler_config"] = {**raw["sampler_config"], "target_accept": 1.5}

    with pytest.raises(PolicyConfigurationError, match="target_accept"):
        BayesianPolicy.from_mapping(raw)


def test_missing_and_invalid_indicator_values_are_never_zero_imputed():
    snapshot = {"rsi_14": "51.5", "adx": None, "spread_pct": "not-a-number"}
    result = extract_indicators(snapshot, ["rsi", "adx", "spread"])
    assert result == {"rsi": 51.5, "adx": None, "spread": None}
    assert finite_number(float("inf")) is None


def test_dataset_hash_is_deterministic_and_order_independent():
    first = _observation(1, rsi=51.0)
    second = _observation(2, rsi=54.0)
    assert canonical_hash([first, second]) == canonical_hash([second, first])


def test_matrix_removes_constants_and_tracks_missingness_explicitly():
    matrix = prepare_matrix(
        [_observation(1, rsi=50.0), _observation(2, rsi=None), _observation(3, rsi=60.0)],
        min_coverage=0.5,
    )
    assert "constant" not in matrix.feature_names
    assert matrix.feature_names == ("rsi", "rsi__missing")
    assert matrix.x.shape == (3, 2)
    assert matrix.x[1, 0] == 0.0
    assert matrix.x[1, 1] == 1.0


def test_evidence_grading_requires_valid_diagnostics_and_configured_diversity():
    policy = {
        "min_effective_sample_size": 100,
        "min_symbols": 2,
        "min_days": 3,
        "weak_probability": 0.70,
        "moderate_probability": 0.85,
        "strong_probability": 0.95,
        "very_strong_probability": 0.99,
        "min_stable_windows": 2,
        "min_consistent_regimes": 2,
        "warning_grade_penalty": 1,
        "very_strong_score": 7,
        "strong_score": 5,
        "moderate_score": 3,
    }
    assert (
        grade_evidence(
            probability_positive=0.995,
            credible_interval=(0.1, 0.4),
            effective_sample_size=500,
            symbol_count=4,
            day_count=10,
            stable_windows=3,
            consistent_regimes=2,
            diagnostic_status=DiagnosticStatus.VALID,
            policy=policy,
        )
        == EvidenceGrade.VERY_STRONG
    )
    assert (
        grade_evidence(
            probability_positive=0.995,
            credible_interval=(0.1, 0.4),
            effective_sample_size=500,
            symbol_count=4,
            day_count=10,
            stable_windows=3,
            consistent_regimes=2,
            diagnostic_status=DiagnosticStatus.NOT_CONVERGED,
            policy=policy,
        )
        == EvidenceGrade.INSUFFICIENT
    )


def test_search_space_is_bounded_around_current_configuration():
    result = build_search_space(
        {"scoring": {"minimum_score": 60}},
        {
            "/scoring/minimum_score": {
                "min": 0,
                "max": 100,
                "max_absolute_delta": 5,
                "step": 1,
                "type": "int",
            }
        },
    )
    assert result[0]["current_value"] == 60
    assert result[0]["low"] == 55
    assert result[0]["high"] == 65


def test_constraints_reject_missing_metrics_and_incompatible_policy():
    policy = {
        "min_trades": 20,
        "min_symbols": 3,
        "min_days": 5,
        "max_symbol_concentration": 0.5,
        "max_drawdown": 10,
        "min_expectancy_oos": 0,
        "min_profit_factor": 1,
        "max_is_oos_degradation": 0.5,
        "min_regime_samples": 5,
    }
    violations = constraint_violations(
        {
            "n_trades": 30,
            "n_symbols": 4,
            "n_days": 6,
            "max_symbol_concentration": 0.4,
            "max_drawdown": 8,
            "expectancy_oos": 0.2,
            "profit_factor_oos": 1.2,
            "is_oos_degradation": 0.2,
            "min_regime_samples": 6,
            "policy_compatible": False,
        },
        policy,
    )
    assert violations == ["operational_policy_incompatible"]


def test_robust_objective_keeps_every_component_auditable():
    metrics = {
        "expectancy_oos": 1.0,
        "profit_factor_oos": 1.2,
        "stability_factor": 0.8,
        "diversity_factor": 0.7,
        "regime_consistency": 0.6,
        "sl_rate": 0.3,
        "max_drawdown": 0.4,
        "max_symbol_concentration": 0.2,
        "is_oos_degradation": 0.1,
    }
    weights = {
        "expectancy": 1.0,
        "profit_factor": 1.0,
        "stability": 1.0,
        "diversity": 1.0,
        "regime_consistency": 1.0,
        "sl_rate": 1.0,
        "drawdown": 1.0,
        "concentration": 1.0,
        "overfit": 1.0,
        "complexity": 0.1,
        "trial_volume": 0.001,
    }
    score, components = robust_score(
        metrics, changed_parameters=2, total_trials=10, weights=weights
    )
    assert components["robust_score"] == score
    assert set(components) >= {
        "expectancy_component",
        "profit_factor_component",
        "drawdown_penalty",
        "concentration_penalty",
        "overfit_penalty",
        "complexity_penalty",
        "trial_volume_penalty",
    }


def test_temporal_split_preserves_final_holdout_and_embargo():
    observations = tuple(_observation(index, rsi=50 + index) for index in range(12))
    result = purged_temporal_split(
        observations,
        discovery_fraction=0.5,
        validation_fraction=0.25,
        embargo_seconds=1,
    )
    assert result.discovery[-1].occurred_at < result.validation[0].occurred_at
    assert result.validation[-1].occurred_at < result.final_holdout[0].occurred_at
    assert result.final_holdout


@pytest.mark.asyncio
async def test_replay_adapter_refuses_repository_stub_without_side_effects():
    result = await ProfileReplayAdapter().run(
        base_profile_config={},
        candidate_config={"scoring": {"minimum_score": 65}},
        dataset_hash="abc",
    )
    assert result.status == "REPLAY_FAILED"
    assert result.supported is False
    assert result.operational_mutation is False
    assert result.orders_created == 0


def test_new_package_has_no_ml_or_trading_imports():
    package = Path(__file__).resolve().parents[1] / "app" / "profile_bayesian"
    forbidden = {
        "app.ml",
        "app.tasks.pipeline_scan",
        "app.tasks.execute_buy",
        "app.services.decision_orchestrator",
    }
    offenders: list[str] = []
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(any(name.startswith(prefix) for prefix in forbidden) for name in names):
                offenders.append(str(path))
    assert offenders == []


def test_importing_api_contract_does_not_import_scientific_runtime():
    before = set(sys.modules)
    __import__("app.api.profile_bayesian_intelligence")
    newly_loaded = set(sys.modules) - before
    assert "pymc" not in newly_loaded
    assert "arviz" not in newly_loaded


class _EmptyIndicatorEffectsResult:
    def mappings(self):
        return self

    def all(self):
        return []


class _IndicatorEffectsDb:
    def __init__(self):
        self.statement = None
        self.params = None

    async def execute(self, statement, params):
        self.statement = statement
        self.params = params
        return _EmptyIndicatorEffectsResult()


@pytest.mark.asyncio
async def test_indicator_effects_casts_optional_run_id_for_postgres(monkeypatch):
    async def _owned_profile(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(bayesian_api, "_profile_for_user", _owned_profile)
    db = _IndicatorEffectsDb()

    result = await bayesian_api.indicator_effects(
        profile_id=uuid4(),
        analysis_run_id=None,
        limit=100,
        db=db,
        user_id=uuid4(),
    )

    sql = str(db.statement)
    assert result == {"items": []}
    assert "CAST(:run_id AS UUID) IS NULL" in sql
    assert "e.analysis_run_id = CAST(:run_id AS UUID)" in sql
    assert db.params["run_id"] is None


class _SequenceMappings:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _DatasetDb:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return _SequenceMappings(self.results.pop(0))


@pytest.mark.asyncio
async def test_dataset_builder_uses_completed_rows_and_dominant_policy():
    profile_id = uuid4()
    user_id = uuid4()
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    dominant = {
        "barrier_contract_version": "shadow_atr_dynamic_v2",
        "tp_pct": 0.5,
        "sl_pct": 0.5,
        "timeout_candles": 1440,
        "direction": "SPOT",
        "row_count": 1404,
    }
    secondary = {
        "barrier_contract_version": "shadow_fixed_v1",
        "tp_pct": 1.0,
        "sl_pct": 1.0,
        "timeout_candles": 1440,
        "direction": "SPOT",
        "row_count": 77,
    }
    selected_row = {
        "id": uuid4(),
        "event_id": uuid4(),
        "profile_id": profile_id,
        "profile_version_id": None,
        "symbol": "BTC_USDT",
        "timeframe": "5m",
        "entry_timestamp": now - timedelta(minutes=5),
        "completed_at": now,
        "outcome": "TP_HIT",
        "pnl_pct": 0.5,
        "source": "L3",
        "direction": "SPOT",
        "tp_pct": 0.5,
        "sl_pct": 0.5,
        "timeout_candles": 1440,
        "barrier_contract_version": "shadow_atr_dynamic_v2",
        "features_snapshot": {"rsi": 55.0, "adx": 25.0},
        "exit_metrics_json": {"net_return_pct": 0.45},
    }
    db = _DatasetDb([[dominant, secondary], [selected_row]])

    dataset = await BayesianDatasetBuilder().build(
        db,
        user_id=user_id,
        profile_id=profile_id,
        window_from=now - timedelta(days=30),
        window_to=now + timedelta(days=1),
        max_trades=2000,
    )

    group_sql, group_params = db.calls[0]
    row_sql, row_params = db.calls[1]
    assert "st.status = 'COMPLETED'" in group_sql
    assert "CAST(:profile_version_id AS UUID)" in group_sql
    assert "st.status = 'COMPLETED'" in row_sql
    assert group_params["profile_version_id"] is None
    assert row_params["barrier_contract_version"] == "shadow_atr_dynamic_v2"
    assert dataset.manifest["inclusion"]["policy_selection"] == (
        "largest_compatible_group"
    )
    assert dataset.manifest["exclusion"]["incompatible_policy_rows"] == 77
    assert len(dataset.observations) == 1


class _PolicyConfigService:
    def __init__(self):
        self.saved = None

    async def get_config(self, *_args, **_kwargs):
        return {}

    async def update_config(self, **kwargs):
        self.saved = kwargs
        return kwargs["new_json"]


@pytest.mark.asyncio
async def test_policy_activation_persists_analysis_only_template(monkeypatch):
    service = _PolicyConfigService()
    monkeypatch.setattr(bayesian_api, "config_service", service)

    result = await bayesian_api.activate_analysis_only_policy(
        db=object(),
        user_id=uuid4(),
    )

    assert result["configured"] is True
    assert result["created"] is True
    assert result["summary"]["policy_version"] == "analysis_only_v1"
    assert service.saved["config_type"] == "profile_bayesian"
    assert (
        service.saved["new_json"]["permissions"][
            "profile_bayesian.run_optimization"
        ]
        is False
    )
