from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .hashing import canonical_hash


@dataclass(frozen=True)
class ModuleCapability:
    module_key: str
    version: str
    entities: tuple[str, ...]
    read_tools: tuple[str, ...]
    write_tools: tuple[str, ...]
    dependencies: tuple[str, ...]
    freshness_sla_seconds: int | None
    risk_class: str
    tenant_scoped: bool
    content_hash: str
    status: str


def _capability(
    module_key: str,
    *,
    version: str = "1.0.0",
    entities: tuple[str, ...],
    reads: tuple[str, ...],
    writes: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    freshness: int | None = 300,
    risk_class: str = "READ_ONLY",
) -> ModuleCapability:
    payload = {
        "module_key": module_key,
        "version": version,
        "entities": entities,
        "read_tools": reads,
        "write_tools": writes,
        "dependencies": dependencies,
        "freshness_sla_seconds": freshness,
        "risk_class": risk_class,
        "tenant_scoped": True,
        "status": "APPROVED",
    }
    return ModuleCapability(**payload, content_hash=canonical_hash(payload))


_MODULES = (
    _capability(
        "strategy_profiles",
        entities=("profiles", "profile_versions", "profile_suggestions"),
        reads=(
            "strategy_profiles.get_profile", "strategy_profiles.get_effective_configuration_at",
            "strategy_profiles.get_version_history", "strategy_profiles.get_signals",
            "strategy_profiles.get_block_rules", "strategy_profiles.get_filters",
            "strategy_profiles.get_score_binding", "strategy_profiles.compare_versions",
            "strategy_profiles.validate_change_set",
        ),
        writes=("strategy_profiles.create_candidate_version",),
        dependencies=(
            "shadow_portfolio", "score_engine", "global_risk", "strategies",
            "market_regime", "social_score", "ml_models", "audit_version_memory",
        ),
        risk_class="CANDIDATE_ONLY",
    ),
    _capability(
        "ml_models",
        version="1.1.0",
        entities=("ml_model_registry", "ml_evidence_registry", "algorithm_forward_validations"),
        reads=(
            "ml_models.get_registry", "ml_models.get_active_models", "ml_models.get_model_metrics",
            "ml_models.get_feature_contract", "ml_models.get_label_contract",
            "ml_models.get_training_window", "ml_models.get_drift_status",
            "ml_models.get_authority_status", "ml_models.get_recent_experiments",
            "ml_models.get_governed_configuration",
        ),
        dependencies=("shadow_portfolio", "strategy_profiles", "dataset_quality", "audit_version_memory"),
        freshness=900,
    ),
    _capability(
        "shadow_portfolio",
        entities=("shadow_trades", "shadow_trade_report_runs", "shadow_experiments"),
        reads=(
            "shadow.freeze_analysis_dataset", "shadow.get_performance_summary",
            "shadow.get_profile_performance", "shadow.get_score_buckets", "shadow.get_mae_mfe",
            "shadow.get_delayed_tp", "shadow.get_outcome_horizons", "shadow.get_data_quality",
            "shadow.get_indicator_lift",
            "shadow.compare_champion_candidate", "shadow.get_experiment_status",
            "shadow.get_experiment_result",
        ),
        writes=("shadow.create_experiment",),
        dependencies=(
            "strategy_profiles", "score_engine", "global_risk", "strategies", "market_regime",
            "social_score", "ml_models", "audit_version_memory",
        ),
        freshness=120,
        risk_class="SHADOW_ONLY",
    ),
    _capability(
        "score_engine",
        entities=("score_engine_versions", "rule_contribution", "config_profiles"),
        reads=(
            "score_engine.get_effective_configuration_at", "score_engine.get_version_history",
            "score_engine.explain_score", "score_engine.get_component_contributions",
            "score_engine.get_rule_impact", "score_engine.compare_versions",
            "score_engine.estimate_coverage_impact", "score_engine.validate_change_set",
        ),
        writes=("score_engine.create_candidate_version",),
        dependencies=(
            "strategy_profiles", "shadow_portfolio", "global_risk", "strategies",
            "market_regime", "social_score", "audit_version_memory",
        ),
        risk_class="CANDIDATE_ONLY",
    ),
    _capability(
        "global_risk",
        entities=("config_profiles", "positions", "trades", "pools"),
        reads=(
            "global_risk.get_effective_policy", "global_risk.get_policy_version",
            "global_risk.get_current_exposure", "global_risk.validate_recommendation",
            "global_risk.explain_conflicts", "global_risk.get_circuit_breaker_state",
        ),
        dependencies=("strategies", "current_exposure"),
        freshness=60,
        risk_class="HARD_VETO_READ_ONLY",
    ),
    _capability(
        "strategies",
        entities=("config_profiles", "trades", "positions"),
        reads=(
            "strategies.get_execution_policy", "strategies.get_entry_execution_rules",
            "strategies.get_exit_policy", "strategies.get_timeout_policy",
            "strategies.get_trailing_policy", "strategies.get_spot_policy",
            "strategies.get_futures_policy", "strategies.validate_recommendation",
            "strategies.explain_conflicts",
        ),
        dependencies=("global_risk", "spot_invariant"),
        freshness=300,
        risk_class="HARD_VETO_READ_ONLY",
    ),
    _capability(
        "intelligence_runs",
        entities=("ai_graph_runs", "ai_graph_events", "ai_graph_interrupts"),
        reads=(
            "intelligence_runs.get_run", "intelligence_runs.list_runs",
            "intelligence_runs.get_timeline", "intelligence_runs.get_interrupts",
        ),
        dependencies=("audit_version_memory",),
        freshness=30,
    ),
    _capability(
        "social_score",
        version="1.1.0",
        entities=("social_intelligence_runs", "social_asset_observations"),
        reads=(
            "social_score.get_snapshot", "social_score.get_source_breakdown",
            "social_score.get_mentions", "social_score.get_sentiment", "social_score.get_trend",
            "social_score.get_freshness", "social_score.get_coverage",
            "social_score.get_anomaly_flags", "social_score.get_data_quality",
            "social_score.get_governed_configuration",
        ),
        dependencies=("source_quality", "market_regime"),
        freshness=300,
    ),
    _capability(
        "market_regime",
        entities=("regime_history", "indicator_snapshots"),
        reads=(
            "market_regime.get_current", "market_regime.get_history",
            "market_regime.compare_windows", "market_regime.get_confidence",
            "market_regime.get_features",
        ),
        dependencies=("indicator_snapshots",),
        freshness=300,
    ),
    _capability(
        "audit_version_memory",
        entities=("decision_memory", "decision_hypotheses", "ai_change_sets", "config_audit_log"),
        reads=(
            "audit_memory.get_change_lineage", "audit_memory.get_profile_changes",
            "audit_memory.get_score_changes", "audit_memory.get_rollbacks",
            "audit_memory.get_experiment_history", "audit_memory.find_similar_decisions",
        ),
        writes=("audit_memory.persist_hypothesis", "audit_memory.persist_decision_memory"),
        dependencies=(),
        freshness=None,
        risk_class="AUDIT_WRITE",
    ),
)

module_capability_registry = MappingProxyType({item.module_key: item for item in _MODULES})


def export_module_capabilities() -> list[dict]:
    return [
        {
            "module_key": item.module_key,
            "version": item.version,
            "entities": list(item.entities),
            "read_tools": list(item.read_tools),
            "write_tools": list(item.write_tools),
            "dependencies": list(item.dependencies),
            "freshness_sla_seconds": item.freshness_sla_seconds,
            "risk_class": item.risk_class,
            "tenant_scoped": item.tenant_scoped,
            "content_hash": item.content_hash,
            "status": item.status,
        }
        for item in sorted(module_capability_registry.values(), key=lambda value: value.module_key)
    ]
