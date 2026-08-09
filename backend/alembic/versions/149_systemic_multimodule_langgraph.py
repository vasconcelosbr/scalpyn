"""Systemic multi-module registry, contextual memory, and LangGraph v2.

Revision ID: 149_multimodule_langgraph
Revises: 148_langgraph_runtime

The migration is additive. It creates no live authority and does not modify
profile, score, risk, strategy, Spot, ML champion, order, or position pointers.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "149_multimodule_langgraph"
down_revision = "148_langgraph_runtime"
branch_labels = None
depends_on = None

GRAPH_NAMESPACE = uuid.UUID("a42c5ab1-1bda-5e45-ae66-554315834a7d")

MODULE_DEPENDENCIES = {
    "strategy_profiles": ["shadow_portfolio", "score_engine", "global_risk", "strategies", "market_regime", "social_score", "ml_models", "audit_version_memory"],
    "ml_models": ["shadow_portfolio", "strategy_profiles", "dataset_quality", "audit_version_memory"],
    "shadow_portfolio": ["strategy_profiles", "score_engine", "global_risk", "strategies", "market_regime", "social_score", "ml_models", "audit_version_memory"],
    "score_engine": ["strategy_profiles", "shadow_portfolio", "global_risk", "strategies", "market_regime", "social_score", "audit_version_memory"],
    "global_risk": ["strategies", "current_exposure"],
    "strategies": ["global_risk", "spot_invariant"],
    "intelligence_runs": ["audit_version_memory"],
    "social_score": ["source_quality", "market_regime"],
    "market_regime": ["indicator_snapshots"],
    "audit_version_memory": [],
}

MODULE_RISK = {
    "strategy_profiles": "CANDIDATE_ONLY",
    "ml_models": "READ_ONLY",
    "shadow_portfolio": "SHADOW_ONLY",
    "score_engine": "CANDIDATE_ONLY",
    "global_risk": "HARD_VETO_READ_ONLY",
    "strategies": "HARD_VETO_READ_ONLY",
    "intelligence_runs": "READ_ONLY",
    "social_score": "READ_ONLY",
    "market_regime": "READ_ONLY",
    "audit_version_memory": "AUDIT_WRITE",
}

MODULE_ENTITIES = {
    "strategy_profiles": ["profiles", "profile_versions", "profile_suggestions"],
    "ml_models": ["ml_model_registry", "ml_evidence_registry", "algorithm_forward_validations"],
    "shadow_portfolio": ["shadow_trades", "shadow_trade_report_runs", "shadow_experiments"],
    "score_engine": ["score_engine_versions", "rule_contribution", "config_profiles"],
    "global_risk": ["config_profiles", "positions", "trades", "pools"],
    "strategies": ["config_profiles", "trades", "positions"],
    "intelligence_runs": ["ai_graph_runs", "ai_graph_events", "ai_graph_interrupts"],
    "social_score": ["social_intelligence_runs", "social_asset_observations"],
    "market_regime": ["regime_history", "indicator_snapshots"],
    "audit_version_memory": ["decision_memory", "decision_hypotheses", "ai_change_sets", "config_audit_log"],
}

MODULE_READ_TOOLS = {
    "strategy_profiles": ["strategy_profiles.get_profile", "strategy_profiles.get_effective_configuration_at", "strategy_profiles.get_version_history", "strategy_profiles.get_signals", "strategy_profiles.get_block_rules", "strategy_profiles.get_filters", "strategy_profiles.get_score_binding", "strategy_profiles.compare_versions", "strategy_profiles.validate_change_set"],
    "ml_models": ["ml_models.get_registry", "ml_models.get_active_models", "ml_models.get_model_metrics", "ml_models.get_feature_contract", "ml_models.get_label_contract", "ml_models.get_training_window", "ml_models.get_drift_status", "ml_models.get_authority_status", "ml_models.get_recent_experiments"],
    "shadow_portfolio": ["shadow.freeze_analysis_dataset", "shadow.get_performance_summary", "shadow.get_profile_performance", "shadow.get_score_buckets", "shadow.get_mae_mfe", "shadow.get_delayed_tp", "shadow.get_outcome_horizons", "shadow.get_data_quality", "shadow.compare_champion_candidate", "shadow.get_experiment_status", "shadow.get_experiment_result"],
    "score_engine": ["score_engine.get_effective_configuration_at", "score_engine.get_version_history", "score_engine.explain_score", "score_engine.get_component_contributions", "score_engine.get_rule_impact", "score_engine.compare_versions", "score_engine.estimate_coverage_impact", "score_engine.validate_change_set"],
    "global_risk": ["global_risk.get_effective_policy", "global_risk.get_policy_version", "global_risk.get_current_exposure", "global_risk.validate_recommendation", "global_risk.explain_conflicts", "global_risk.get_circuit_breaker_state"],
    "strategies": ["strategies.get_execution_policy", "strategies.get_entry_execution_rules", "strategies.get_exit_policy", "strategies.get_timeout_policy", "strategies.get_trailing_policy", "strategies.get_spot_policy", "strategies.get_futures_policy", "strategies.validate_recommendation", "strategies.explain_conflicts"],
    "intelligence_runs": ["intelligence_runs.get_run", "intelligence_runs.list_runs", "intelligence_runs.get_timeline", "intelligence_runs.get_interrupts"],
    "social_score": ["social_score.get_snapshot", "social_score.get_source_breakdown", "social_score.get_mentions", "social_score.get_sentiment", "social_score.get_trend", "social_score.get_freshness", "social_score.get_coverage", "social_score.get_anomaly_flags", "social_score.get_data_quality"],
    "market_regime": ["market_regime.get_current", "market_regime.get_history", "market_regime.compare_windows", "market_regime.get_confidence", "market_regime.get_features"],
    "audit_version_memory": ["audit_memory.get_change_lineage", "audit_memory.get_profile_changes", "audit_memory.get_score_changes", "audit_memory.get_rollbacks", "audit_memory.get_experiment_history", "audit_memory.find_similar_decisions"],
}

MODULE_WRITE_TOOLS = {
    "strategy_profiles": ["strategy_profiles.create_candidate_version"],
    "ml_models": [],
    "shadow_portfolio": ["shadow.create_experiment"],
    "score_engine": ["score_engine.create_candidate_version"],
    "global_risk": [],
    "strategies": [],
    "intelligence_runs": [],
    "social_score": [],
    "market_regime": [],
    "audit_version_memory": ["audit_memory.persist_hypothesis", "audit_memory.persist_decision_memory"],
}

MODULE_FRESHNESS = {
    "strategy_profiles": 300, "ml_models": 900, "shadow_portfolio": 120,
    "score_engine": 300, "global_risk": 60, "strategies": 300,
    "intelligence_runs": 30, "social_score": 300, "market_regime": 300,
    "audit_version_memory": None,
}

GRAPH_NODES = {
    "systemic-analysis-v2": [
        "load_request", "authorize_tenant", "resolve_origin_module", "resolve_module_dependency_plan",
        "resolve_provider_model", "resolve_prompt", "freeze_dataset", "resolve_configuration_bundle",
        "run_data_quality_gate", "load_strategy_profiles", "load_shadow", "load_score_engine",
        "load_global_risk", "load_strategies", "load_ml_evidence", "load_social_score",
        "load_market_regime", "load_audit_memory", "run_module_conflict_checks", "plan_tools",
        "execute_tools", "assemble_evidence", "invoke_provider", "validate_output", "persist_result", "complete",
    ],
    "root-cause-audit-v2": [
        "load_request", "authorize_tenant", "identify_change_set", "load_before_after",
        "validate_contract_equivalence", "compare_data_quality", "compare_market_regime",
        "compare_social_context", "compare_profile_version", "compare_score_version",
        "compare_risk_policy", "compare_strategy_policy", "compare_ml_authority", "run_paired_replay",
        "classify_root_cause", "assemble_evidence", "invoke_provider", "validate_output", "persist_result", "complete",
    ],
    "regenerative-shadow-v2": [
        "receive_degradation", "freeze_comparable_dataset", "resolve_bundle", "classify_root_cause",
        "create_hypothesis", "retrieve_contextual_memory", "block_repeated_failed_path", "design_ablation",
        "validate_risk_and_strategy", "interrupt_candidate_approval", "create_profile_candidate_version",
        "create_score_candidate_version", "start_shadow_experiment", "interrupt_wait_evidence",
        "resume_from_shadow_event", "evaluate_deterministically", "interrupt_final_decision",
        "keep_reject_or_create_rollback_version", "persist_outcome", "persist_contextual_memory", "complete",
    ],
    "copilot-systemic-v2": [
        "load_request", "authorize_tenant", "resolve_origin_module", "resolve_module_dependency_plan",
        "resolve_provider_model", "resolve_prompt", "load_strategy_profiles", "load_shadow",
        "load_score_engine", "load_global_risk", "load_strategies", "load_ml_evidence",
        "load_social_score", "load_market_regime", "load_audit_memory", "plan_tools", "execute_tools",
        "assemble_evidence", "invoke_provider", "validate_output", "persist_result", "complete",
    ],
}


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _graph_rows() -> list[dict]:
    approved_at = datetime(2026, 8, 8, tzinfo=timezone.utc)
    rows = []
    for graph_key, nodes in GRAPH_NODES.items():
        edges = [[nodes[index], nodes[index + 1]] for index in range(len(nodes) - 1)]
        payload = {
            "graph_key": graph_key,
            "semantic_version": "2.0.0",
            "state_schema_version": "scalpyn-graph-state-v2",
            "node_manifest": nodes,
            "edge_manifest": edges,
            "tool_policy_version": "systemic-multimodule-tool-policy-v1",
        }
        rows.append({
            "id": uuid.uuid5(GRAPH_NAMESPACE, f"{graph_key}@2.0.0"),
            **payload,
            "status": "APPROVED",
            "content_hash": _canonical_hash(payload),
            "code_revision": revision,
            "created_at": approved_at,
            "approved_at": approved_at,
        })
    return rows


def _module_rows() -> list[dict]:
    approved_at = datetime(2026, 8, 8, tzinfo=timezone.utc)
    rows = []
    for module_key, dependencies in MODULE_DEPENDENCIES.items():
        content_payload = {
            "module_key": module_key,
            "version": "1.0.0",
            "entities": MODULE_ENTITIES[module_key],
            "read_tools": MODULE_READ_TOOLS[module_key],
            "write_tools": MODULE_WRITE_TOOLS[module_key],
            "dependencies": dependencies,
            "freshness_sla_seconds": MODULE_FRESHNESS[module_key],
            "risk_class": MODULE_RISK[module_key],
            "tenant_scoped": True,
            "status": "APPROVED",
        }
        rows.append({
            "id": uuid.uuid5(GRAPH_NAMESPACE, f"module:{module_key}@1.0.0"),
            "module_key": module_key,
            "semantic_version": "1.0.0",
            "entities": content_payload["entities"],
            "read_tools": content_payload["read_tools"],
            "write_tools": content_payload["write_tools"],
            "dependencies": dependencies,
            "freshness_sla_seconds": content_payload["freshness_sla_seconds"],
            "risk_class": content_payload["risk_class"],
            "tenant_scoped": True,
            "status": "APPROVED",
            "content_hash": _canonical_hash(content_payload),
            "created_at": approved_at,
            "approved_at": approved_at,
        })
    return rows


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    op.create_table(
        "ai_model_approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("max_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("input_cost_per_million", sa.Numeric(18, 8), nullable=False),
        sa.Column("output_cost_per_million", sa.Numeric(18, 8), nullable=False),
        sa.Column("max_output_tokens", sa.Integer, nullable=False),
        sa.Column("pricing_source_url", sa.Text, nullable=False),
        sa.Column("pricing_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pricing_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("approval_phrase_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("approved_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.CheckConstraint("max_cost_usd > 0", name="ck_ai_model_approval_positive_cost"),
        sa.CheckConstraint("input_cost_per_million >= 0", name="ck_ai_model_approval_input_price"),
        sa.CheckConstraint("output_cost_per_million >= 0", name="ck_ai_model_approval_output_price"),
        sa.CheckConstraint("max_output_tokens > 0", name="ck_ai_model_approval_output_cap"),
        sa.CheckConstraint("status = 'APPROVED'", name="ck_ai_model_approval_immutable_status"),
    )
    op.create_index(
        "ix_ai_model_approval_tenant_model_time",
        "ai_model_approvals",
        ["tenant_id", "provider", "model", "expires_at"],
    )

    modules = op.create_table(
        "ai_module_capabilities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("module_key", sa.String(120), nullable=False),
        sa.Column("semantic_version", sa.String(40), nullable=False),
        sa.Column("entities", JSONB, nullable=False),
        sa.Column("read_tools", JSONB, nullable=False),
        sa.Column("write_tools", JSONB, nullable=False),
        sa.Column("dependencies", JSONB, nullable=False),
        sa.Column("freshness_sla_seconds", sa.Integer),
        sa.Column("risk_class", sa.String(48), nullable=False),
        sa.Column("tenant_scoped", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("deprecated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("module_key", "semantic_version", name="uq_ai_module_capability_key_version"),
        sa.CheckConstraint("status IN ('DRAFT','APPROVED','DEPRECATED','BLOCKED')", name="ck_ai_module_capability_status"),
    )
    op.bulk_insert(modules, _module_rows())
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION prevent_approved_ai_module_capability_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status = 'APPROVED' AND (
            NEW.module_key IS DISTINCT FROM OLD.module_key OR
            NEW.semantic_version IS DISTINCT FROM OLD.semantic_version OR
            NEW.entities IS DISTINCT FROM OLD.entities OR
            NEW.read_tools IS DISTINCT FROM OLD.read_tools OR
            NEW.write_tools IS DISTINCT FROM OLD.write_tools OR
            NEW.dependencies IS DISTINCT FROM OLD.dependencies OR
            NEW.freshness_sla_seconds IS DISTINCT FROM OLD.freshness_sla_seconds OR
            NEW.risk_class IS DISTINCT FROM OLD.risk_class OR
            NEW.tenant_scoped IS DISTINCT FROM OLD.tenant_scoped OR
            NEW.content_hash IS DISTINCT FROM OLD.content_hash OR
            NEW.status IS DISTINCT FROM OLD.status OR
            NEW.created_at IS DISTINCT FROM OLD.created_at OR
            NEW.approved_at IS DISTINCT FROM OLD.approved_at OR
            NEW.deprecated_at IS DISTINCT FROM OLD.deprecated_at
          ) THEN RAISE EXCEPTION 'approved AI module capability is immutable'; END IF;
          RETURN NEW;
        END $$;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_ai_module_capability_immutable
        BEFORE UPDATE ON ai_module_capabilities FOR EACH ROW
        EXECUTE FUNCTION prevent_approved_ai_module_capability_mutation()
    """))

    op.create_table(
        "ai_tool_evidence",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_request_id", UUID(as_uuid=True), sa.ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_call_audit_id", UUID(as_uuid=True), sa.ForeignKey("ai_tool_call_audits.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("module_key", sa.String(120), nullable=False),
        sa.Column("tool_name", sa.String(160), nullable=False),
        sa.Column("output_json", JSONB, nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("freshness_json", JSONB),
        sa.Column("quality", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_ai_tool_evidence_request_module",
        "ai_tool_evidence",
        ["tenant_id", "ai_request_id", "module_key", "created_at"],
    )

    op.add_column("ai_dataset_snapshots", sa.Column("origin_module", sa.String(120)))
    op.add_column("ai_dataset_snapshots", sa.Column("module_context_refs", JSONB))
    op.add_column("ai_dataset_snapshots", sa.Column("context_manifest", JSONB))
    op.add_column("decision_memory", sa.Column("mutation_fingerprint", sa.String(64)))
    op.add_column("decision_memory", sa.Column("context_fingerprint", sa.String(64)))
    op.add_column("decision_memory", sa.Column("context_json", JSONB))
    op.create_index(
        "ix_decision_memory_tenant_context_mutation",
        "decision_memory",
        ["tenant_id", "context_fingerprint", "mutation_fingerprint", "created_at"],
    )

    graph_table = sa.table(
        "ai_graph_definitions",
        sa.column("id", UUID(as_uuid=True)), sa.column("graph_key", sa.String),
        sa.column("semantic_version", sa.String), sa.column("state_schema_version", sa.String),
        sa.column("status", sa.String), sa.column("content_hash", sa.String),
        sa.column("code_revision", sa.String), sa.column("node_manifest", JSONB),
        sa.column("edge_manifest", JSONB), sa.column("tool_policy_version", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)), sa.column("approved_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(graph_table, _graph_rows())


def downgrade() -> None:
    graph_ids = [row["id"] for row in _graph_rows()]
    graph_table = sa.table("ai_graph_definitions", sa.column("id", UUID(as_uuid=True)))
    op.execute(graph_table.delete().where(graph_table.c.id.in_(graph_ids)))
    op.drop_index("ix_decision_memory_tenant_context_mutation", table_name="decision_memory")
    op.drop_column("decision_memory", "context_json")
    op.drop_column("decision_memory", "context_fingerprint")
    op.drop_column("decision_memory", "mutation_fingerprint")
    op.drop_column("ai_dataset_snapshots", "context_manifest")
    op.drop_column("ai_dataset_snapshots", "module_context_refs")
    op.drop_column("ai_dataset_snapshots", "origin_module")
    op.drop_index("ix_ai_tool_evidence_request_module", table_name="ai_tool_evidence")
    op.drop_table("ai_tool_evidence")
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_ai_module_capability_immutable ON ai_module_capabilities"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_approved_ai_module_capability_mutation()"))
    op.drop_table("ai_module_capabilities")
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ai_model_approval_tenant_model_time"))
    op.execute(sa.text("DROP TABLE IF EXISTS ai_model_approvals"))
