"""Tenant-safe LangGraph runtime metadata and dedicated checkpoint schema.

Revision ID: 148_langgraph_runtime
Revises: 147_systemic_ai_foundation
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "148_langgraph_runtime"
down_revision = "147_systemic_ai_foundation"
branch_labels = None
depends_on = None

GRAPH_NAMESPACE = uuid.UUID("a42c5ab1-1bda-5e45-ae66-554315834a7d")
STATE_SCHEMA_VERSION = "scalpyn-graph-state-v1"
TOOL_POLICY_VERSION = "systemic-tool-policy-v1"

GRAPH_NODES = {
    "systemic-analysis-v1": [
        "load_request", "authorize_tenant", "resolve_provider_model", "resolve_prompt",
        "freeze_canonical_dataset", "resolve_configuration_bundle", "run_data_quality_gate",
        "load_related_module_context", "retrieve_decision_memory", "plan_typed_tools",
        "execute_readonly_tools", "assemble_evidence", "invoke_provider",
        "validate_structured_output", "persist_result_usage_audit", "complete",
    ],
    "root-cause-audit-v1": [
        "load_request", "authorize_tenant", "identify_change_window", "load_before_after_versions",
        "validate_comparability", "compare_market_regime", "compare_data_quality",
        "compare_symbol_profile_concentration", "compare_exit_policy",
        "compare_model_feature_contract", "run_paired_replay_when_available",
        "classify_root_cause", "generate_evidence_bound_diagnosis", "persist_result_usage_audit",
        "complete",
    ],
    "regenerative-shadow-v1": [
        "detect_or_receive_degradation", "validate_dataset_and_bundle", "classify_root_cause",
        "create_hypothesis", "retrieve_similar_decision_memory", "check_do_not_repeat_context",
        "design_ablation_candidates", "interrupt_candidate_approval",
        "create_immutable_candidate_versions", "start_shadow_experiment",
        "interrupt_wait_for_shadow_evidence", "resume_from_shadow_event",
        "evaluate_champion_challenger", "propose_keep_reject_or_rollback",
        "interrupt_final_decision", "apply_shadow_only_pointer_or_create_rollback_version",
        "persist_experiment_outcome", "persist_decision_memory", "complete",
    ],
    "copilot-systemic-v1": [
        "load_request", "authorize_tenant", "resolve_provider_model", "resolve_prompt",
        "load_related_module_context", "retrieve_decision_memory", "plan_typed_tools",
        "execute_readonly_tools", "assemble_evidence", "invoke_provider",
        "validate_structured_output", "persist_result_usage_audit", "complete",
    ],
}


def _definition_rows() -> list[dict]:
    seeded_at = datetime(2026, 8, 7, tzinfo=timezone.utc)
    rows = []
    for graph_key, nodes in GRAPH_NODES.items():
        edges = [[nodes[index], nodes[index + 1]] for index in range(len(nodes) - 1)]
        payload = {
            "graph_key": graph_key,
            "semantic_version": "1.0.0",
            "state_schema_version": STATE_SCHEMA_VERSION,
            "node_manifest": nodes,
            "edge_manifest": edges,
            "tool_policy_version": TOOL_POLICY_VERSION,
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        rows.append({
            "id": uuid.uuid5(GRAPH_NAMESPACE, f"{graph_key}@1.0.0"),
            **payload,
            "status": "APPROVED",
            "content_hash": content_hash,
            "code_revision": "148_langgraph_runtime",
            "created_at": seeded_at,
            "approved_at": seeded_at,
        })
    return rows


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS langgraph_runtime"))

    definitions = op.create_table(
        "ai_graph_definitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("graph_key", sa.String(120), nullable=False),
        sa.Column("semantic_version", sa.String(40), nullable=False),
        sa.Column("state_schema_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("code_revision", sa.String(80), nullable=False),
        sa.Column("node_manifest", JSONB, nullable=False),
        sa.Column("edge_manifest", JSONB, nullable=False),
        sa.Column("tool_policy_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("deprecated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("graph_key", "semantic_version", name="uq_ai_graph_definition_key_version"),
        sa.CheckConstraint("status IN ('DRAFT','APPROVED','DEPRECATED','BLOCKED')", name="ck_ai_graph_definition_status"),
    )
    op.bulk_insert(definitions, _definition_rows())
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION prevent_approved_ai_graph_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status = 'APPROVED' AND (
            NEW.graph_key IS DISTINCT FROM OLD.graph_key OR
            NEW.semantic_version IS DISTINCT FROM OLD.semantic_version OR
            NEW.state_schema_version IS DISTINCT FROM OLD.state_schema_version OR
            NEW.content_hash IS DISTINCT FROM OLD.content_hash OR
            NEW.code_revision IS DISTINCT FROM OLD.code_revision OR
            NEW.node_manifest IS DISTINCT FROM OLD.node_manifest OR
            NEW.edge_manifest IS DISTINCT FROM OLD.edge_manifest OR
            NEW.tool_policy_version IS DISTINCT FROM OLD.tool_policy_version
          ) THEN RAISE EXCEPTION 'approved AI graph definition is immutable'; END IF;
          RETURN NEW;
        END $$;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_ai_graph_definition_immutable
        BEFORE UPDATE ON ai_graph_definitions FOR EACH ROW
        EXECUTE FUNCTION prevent_approved_ai_graph_mutation()
    """))

    op.create_table(
        "ai_graph_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("ai_request_id", UUID(as_uuid=True), sa.ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_job_id", UUID(as_uuid=True), sa.ForeignKey("ai_jobs.id", ondelete="SET NULL")),
        sa.Column("graph_definition_id", UUID(as_uuid=True), sa.ForeignKey("ai_graph_definitions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("checkpoint_namespace", sa.String(120), nullable=False, server_default="scalpyn"),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="QUEUED"),
        sa.Column("current_node", sa.String(160)),
        sa.Column("state_schema_version", sa.String(80), nullable=False),
        sa.Column("authority", sa.String(40), nullable=False),
        sa.Column("lease_owner", sa.String(160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_reason", sa.String(160)),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("last_error_safe_message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_ai_graph_run_tenant_idempotency"),
        sa.CheckConstraint("authority IN ('ANALYSIS_ONLY','PROPOSAL_ONLY','CANDIDATE_ONLY','SHADOW_ONLY')", name="ck_ai_graph_run_authority"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','INTERRUPTED','WAITING_SHADOW','COMPLETED','FAILED','CANCELLED')", name="ck_ai_graph_run_status"),
    )
    op.create_index("ix_ai_graph_run_tenant_created", "ai_graph_runs", ["tenant_id", "created_at"])
    op.create_index("ix_ai_graph_run_status_lease", "ai_graph_runs", ["status", "lease_expires_at"])
    op.create_index("ix_ai_graph_run_ai_request", "ai_graph_runs", ["ai_request_id"])

    op.create_table(
        "ai_graph_interrupts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("graph_run_id", UUID(as_uuid=True), sa.ForeignKey("ai_graph_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interrupt_key", sa.String(160), nullable=False),
        sa.Column("interrupt_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("allowed_edit_fields", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("decision", sa.String(24)),
        sa.Column("decision_payload", JSONB),
        sa.Column("decision_id", UUID(as_uuid=True)),
        sa.Column("actor_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("idempotency_key", sa.String(160)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("graph_run_id", "interrupt_key", name="uq_ai_graph_interrupt_run_key"),
        sa.UniqueConstraint("graph_run_id", "idempotency_key", name="uq_ai_graph_interrupt_run_idempotency"),
        sa.CheckConstraint("status IN ('PENDING','RESOLVED','REJECTED','CANCELLED')", name="ck_ai_graph_interrupt_status"),
        sa.CheckConstraint("decision IS NULL OR decision IN ('approve','reject','edit')", name="ck_ai_graph_interrupt_decision"),
    )
    op.create_index("ix_ai_graph_interrupt_tenant_status", "ai_graph_interrupts", ["tenant_id", "status", "created_at"])

    op.create_table(
        "ai_graph_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("graph_run_id", UUID(as_uuid=True), sa.ForeignKey("ai_graph_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("node_name", sa.String(160)),
        sa.Column("status", sa.String(40)),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("graph_run_id", "event_key", name="uq_ai_graph_event_run_key"),
    )
    op.create_index("ix_ai_graph_event_run_time", "ai_graph_events", ["graph_run_id", "created_at", "id"])
    op.create_index("ix_ai_graph_event_tenant_time", "ai_graph_events", ["tenant_id", "created_at"])

    op.create_table(
        "ai_graph_runtime_metadata",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("metadata_key", sa.String(160), nullable=False, unique=True),
        sa.Column("metadata_value", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("ai_graph_runtime_metadata")
    op.drop_index("ix_ai_graph_event_tenant_time", table_name="ai_graph_events")
    op.drop_index("ix_ai_graph_event_run_time", table_name="ai_graph_events")
    op.drop_table("ai_graph_events")
    op.drop_index("ix_ai_graph_interrupt_tenant_status", table_name="ai_graph_interrupts")
    op.drop_table("ai_graph_interrupts")
    op.drop_index("ix_ai_graph_run_ai_request", table_name="ai_graph_runs")
    op.drop_index("ix_ai_graph_run_status_lease", table_name="ai_graph_runs")
    op.drop_index("ix_ai_graph_run_tenant_created", table_name="ai_graph_runs")
    op.drop_table("ai_graph_runs")
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_ai_graph_definition_immutable ON ai_graph_definitions"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_approved_ai_graph_mutation()"))
    op.drop_table("ai_graph_definitions")
    op.execute(sa.text("DROP SCHEMA IF EXISTS langgraph_runtime CASCADE"))
