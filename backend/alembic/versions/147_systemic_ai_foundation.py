"""Tenant-scoped systemic AI foundation and regenerative ledger.

Revision ID: 147_systemic_ai_foundation
Revises: 146_l3_1200_validation
Create Date: 2026-08-07

All changes are additive. Legacy rows are not assigned synthetic tenant or
lineage identifiers; nullable bridge columns make that absence explicit.
"""

from alembic import context, op
import hashlib
import json
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from uuid import NAMESPACE_URL, uuid5
from datetime import datetime, timezone


revision = "147_systemic_ai_foundation"
down_revision = "146_l3_1200_validation"
branch_labels = None
depends_on = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def _seed_prompts() -> None:
    output_schema = {
        "type": "object", "required": ["analysis", "recommendations"],
        "properties": {"analysis": {"type": "object"}, "recommendations": {"type": "array", "items": {"type": "object"}},
                       "warnings": {"type": "array", "items": {"type": "string"}},
                       "limitations": {"type": "array", "items": {"type": "string"}}},
        "additionalProperties": True,
    }
    definitions = (
        ("profile-suggestion-explanation", "You explain Scalpyn profile suggestions using only supplied evidence. Never invent metrics.",
         "Question: {question}\nSuggestion evidence: {evidence}\nReturn the approved JSON schema.", []),
        ("shadow-detailed-analysis", "You audit tenant-scoped Shadow trades. Association is not causation. Cite trade IDs.",
         "Question: {question}\nFrozen dataset: {dataset}\nConfiguration: {configuration}\nReturn the approved JSON schema.", []),
        ("ai-critic", "You are the analysis-only Scalpyn AI Critic. You have no mutation or live authority.",
         "Question: {question}\nCanonical context: {dataset}\nReturn the approved JSON schema.", []),
        ("copilot", "You are Scalpyn Co-Pilot. Tool policy is enforced by code; live writes are denied.",
         "Question: {question}\nScreen context: {context}\nReturn the approved JSON schema.",
         ["shadow.get_performance_summary", "profiles.get_effective_configuration", "audit.get_change_lineage"]),
    )
    seeded_at = datetime.now(timezone.utc)

    def literal(value: object) -> str:
        return str(value).replace("'", "''")

    for key, system, user, tools in definitions:
        version = "1.0.0"
        payload = {"prompt_key": key, "semantic_version": version, "system_template": system, "user_template": user,
                   "input_schema_json": {"type": "object"}, "output_schema_json": output_schema,
                   "tool_policy_json": {"allowlist": tools, "live_write": False},
                   "provider_constraints_json": {"required_capabilities": ["text", "structured_output"]}}
        content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        prompt_id = uuid5(NAMESPACE_URL, f"scalpyn:prompt:{key}:{version}")
        input_schema = json.dumps(payload["input_schema_json"], separators=(",", ":"), ensure_ascii=False)
        serialized_output = json.dumps(payload["output_schema_json"], separators=(",", ":"), ensure_ascii=False)
        tool_policy = json.dumps(payload["tool_policy_json"], separators=(",", ":"), ensure_ascii=False)
        constraints = json.dumps(payload["provider_constraints_json"], separators=(",", ":"), ensure_ascii=False)
        statement = f"""
            INSERT INTO ai_prompt_versions (
                id, prompt_key, semantic_version, status, system_template, user_template,
                input_schema_json, output_schema_json, tool_policy_json,
                provider_constraints_json, content_hash, created_at, approved_at
            ) VALUES (
                '{prompt_id}'::uuid, '{literal(key)}', '{version}', 'APPROVED',
                '{literal(system)}', '{literal(user)}',
                '{literal(input_schema)}'::jsonb, '{literal(serialized_output)}'::jsonb,
                '{literal(tool_policy)}'::jsonb, '{literal(constraints)}'::jsonb,
                '{content_hash}', '{seeded_at.isoformat()}'::timestamptz,
                '{seeded_at.isoformat()}'::timestamptz
            )
        """
        if context.is_offline_mode():
            # SQLAlchemy text() treats JSON fragments such as `:true` as bind
            # parameters. Escaping colons preserves literal JSON in offline SQL.
            op.execute(sa.text(statement.replace(":", r"\:")))
        else:
            # The seed contains only static, source-controlled definitions.
            # exec_driver_sql bypasses SQLAlchemy's bind parser for JSON text.
            op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    op.create_table(
        "ai_prompt_versions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("prompt_key", sa.String(160), nullable=False),
        sa.Column("semantic_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("system_template", sa.Text, nullable=False),
        sa.Column("user_template", sa.Text, nullable=False),
        sa.Column("input_schema_json", JSONB, nullable=False),
        sa.Column("output_schema_json", JSONB, nullable=False),
        sa.Column("tool_policy_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("provider_constraints_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("approved_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("deprecated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("prompt_key", "semantic_version", name="uq_ai_prompt_key_version"),
        sa.CheckConstraint("status IN ('DRAFT','APPROVED','DEPRECATED')", name="ck_ai_prompt_status"),
    )
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION prevent_approved_ai_prompt_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status = 'APPROVED' AND (
            NEW.system_template IS DISTINCT FROM OLD.system_template OR
            NEW.user_template IS DISTINCT FROM OLD.user_template OR
            NEW.input_schema_json IS DISTINCT FROM OLD.input_schema_json OR
            NEW.output_schema_json IS DISTINCT FROM OLD.output_schema_json OR
            NEW.tool_policy_json IS DISTINCT FROM OLD.tool_policy_json OR
            NEW.provider_constraints_json IS DISTINCT FROM OLD.provider_constraints_json OR
            NEW.content_hash IS DISTINCT FROM OLD.content_hash
          ) THEN RAISE EXCEPTION 'approved AI prompt content is immutable'; END IF;
          RETURN NEW;
        END $$;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_ai_prompt_immutable
        BEFORE UPDATE ON ai_prompt_versions FOR EACH ROW
        EXECUTE FUNCTION prevent_approved_ai_prompt_mutation()
    """))
    _seed_prompts()

    op.create_table(
        "ai_model_aliases",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("alias", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("real_model_id", sa.String(200), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("capabilities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.UniqueConstraint("provider", "alias", name="uq_ai_model_alias_provider"),
    )
    op.create_table(
        "ai_model_resolutions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_provider", sa.String(40)), sa.Column("requested_model", sa.String(200)),
        sa.Column("configured_provider", sa.String(40)), sa.Column("configured_model", sa.String(200)),
        sa.Column("effective_provider", sa.String(40), nullable=False),
        sa.Column("effective_model", sa.String(200), nullable=False),
        sa.Column("catalog_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("capabilities", JSONB, nullable=False),
        sa.Column("resolution_policy_version", sa.String(80), nullable=False),
        sa.Column("resolution_reason", sa.String(120), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ai_model_resolution_tenant_resolved", "ai_model_resolutions", ["tenant_id", "resolved_at"])

    op.create_table(
        "ai_configuration_bundles",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", UUID, sa.ForeignKey("profiles.id", ondelete="SET NULL")),
        sa.Column("profile_version_id", UUID, sa.ForeignKey("profile_versions.id", ondelete="SET NULL")),
        sa.Column("score_engine_version_id", UUID, sa.ForeignKey("score_engine_versions.id", ondelete="SET NULL")),
        sa.Column("lineage_refs", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("bundle_json", JSONB, nullable=False), sa.Column("bundle_hash", sa.String(64), nullable=False),
        sa.Column("lineage_status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ai_bundle_tenant_created", "ai_configuration_bundles", ["tenant_id", "created_at"])
    op.create_table(
        "ai_dataset_snapshots",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_version", sa.String(80), nullable=False),
        sa.Column("source_tables", JSONB, nullable=False), sa.Column("source_labels", JSONB, nullable=False),
        sa.Column("event_identity_contract", sa.String(160), nullable=False),
        sa.Column("outcome_contract", sa.String(160), nullable=False), sa.Column("time_anchor", sa.String(80), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False), sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filters", JSONB, nullable=False), sa.Column("exclusions", JSONB, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False), sa.Column("row_ids_hash", sa.String(64), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False), sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("configuration_bundle_id", UUID, sa.ForeignKey("ai_configuration_bundles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quality_status", sa.String(48), nullable=False), sa.Column("quality_findings", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ai_dataset_tenant_created", "ai_dataset_snapshots", ["tenant_id", "created_at"])

    op.create_table(
        "ai_requests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("origin_module", sa.String(120), nullable=False), sa.Column("origin_view", sa.String(200)),
        sa.Column("analysis_mode", sa.String(40), nullable=False), sa.Column("authority", sa.String(40), nullable=False),
        sa.Column("question_hash", sa.String(64), nullable=False), sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column("model_resolution_id", UUID, sa.ForeignKey("ai_model_resolutions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("prompt_version_id", UUID, sa.ForeignKey("ai_prompt_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("dataset_snapshot_id", UUID, sa.ForeignKey("ai_dataset_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("configuration_bundle_id", UUID, sa.ForeignKey("ai_configuration_bundles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "correlation_id", name="uq_ai_request_tenant_correlation"),
    )
    op.create_index("ix_ai_request_tenant_created", "ai_requests", ["tenant_id", "created_at"])
    op.create_table(
        "ai_jobs",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_request_id", UUID, sa.ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(120), nullable=False), sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(160)), sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"), sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("retry_after", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_reason", sa.String(160)), sa.Column("last_error_code", sa.String(80)),
        sa.Column("last_error_safe_message", sa.Text),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_ai_job_tenant_dedupe"),
    )
    op.create_index("ix_ai_job_tenant_status_time", "ai_jobs", ["tenant_id", "status", "queued_at"])
    op.create_index("ix_ai_job_lease_expiry", "ai_jobs", ["status", "lease_expires_at"])
    op.create_table(
        "ai_results",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_request_id", UUID, sa.ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("result_json", JSONB, nullable=False),
        sa.Column("terminal_reason", sa.String(160)), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "ai_request_id", name="uq_ai_result_tenant_request"),
    )
    op.create_table(
        "ai_usage_records",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_request_id", UUID, sa.ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False), sa.Column("model", sa.String(200), nullable=False),
        sa.Column("module", sa.String(120), nullable=False), sa.Column("tokens_input", sa.Integer, nullable=False),
        sa.Column("tokens_output", sa.Integer, nullable=False), sa.Column("estimated_cost", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("actual_cost", sa.Numeric(18, 8), nullable=False, server_default="0"), sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("pricing_snapshot_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ai_usage_tenant_created", "ai_usage_records", ["tenant_id", "created_at"])
    op.create_table(
        "ai_budget_policies",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False), sa.Column("model", sa.String(200)), sa.Column("module", sa.String(120)),
        sa.Column("daily_token_limit", sa.Integer), sa.Column("monthly_token_limit", sa.Integer),
        sa.Column("request_token_limit", sa.Integer, nullable=False), sa.Column("null_limit_policy", sa.String(20), nullable=False, server_default="DENY"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "provider", "model", "module", name="uq_ai_budget_scope"),
    )
    op.create_table(
        "ai_tool_call_audits",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_request_id", UUID, sa.ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(160), nullable=False), sa.Column("tool_version", sa.String(40), nullable=False),
        sa.Column("side_effect", sa.String(40), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False), sa.Column("output_hash", sa.String(64)),
        sa.Column("denial_reason", sa.String(160)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ai_tool_audit_tenant_created", "ai_tool_call_audits", ["tenant_id", "created_at"])

    # Generic regenerative ledger. These tables carry no auto-apply authority.
    for table_name in ("decision_hypotheses", "ai_change_sets", "regeneration_runs", "experiment_links",
                       "decision_memory", "context_fingerprints", "mutation_fingerprints", "causal_evidence_refs"):
        op.create_table(
            table_name,
            sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("tenant_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ai_request_id", UUID, sa.ForeignKey("ai_requests.id", ondelete="SET NULL")),
            sa.Column("dataset_snapshot_id", UUID, sa.ForeignKey("ai_dataset_snapshots.id", ondelete="RESTRICT")),
            sa.Column("configuration_bundle_id", UUID, sa.ForeignKey("ai_configuration_bundles.id", ondelete="RESTRICT")),
            sa.Column("authority", sa.String(40), nullable=False, server_default="ANALYSIS_ONLY"),
            sa.Column("status", sa.String(40), nullable=False, server_default="DRAFT"),
            sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index(f"ix_{table_name}_tenant_created", table_name, ["tenant_id", "created_at"])

    for table_name in ("shadow_trade_analysis_jobs", "profile_ai_reviews", "profile_suggestions"):
        op.add_column(table_name, sa.Column("tenant_id", UUID, nullable=True))
        op.add_column(table_name, sa.Column("ai_request_id", UUID, nullable=True))
        op.create_index(f"ix_{table_name}_tenant_ai_request", table_name, ["tenant_id", "ai_request_id"])
    for column in (
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)), sa.Column("lease_owner", sa.String(160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)), sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"), sa.Column("retry_after", sa.DateTime(timezone=True)),
        sa.Column("terminal_reason", sa.String(160)), sa.Column("last_error_code", sa.String(80)),
        sa.Column("last_error_safe_message", sa.Text),
    ):
        op.add_column("shadow_trade_analysis_jobs", column)
    op.add_column("shadow_trades", sa.Column("configuration_bundle_id", UUID, nullable=True))
    op.create_index("ix_shadow_trades_configuration_bundle", "shadow_trades", ["configuration_bundle_id"])


def downgrade() -> None:
    op.drop_index("ix_shadow_trades_configuration_bundle", table_name="shadow_trades")
    op.drop_column("shadow_trades", "configuration_bundle_id")
    for name in ("last_error_safe_message", "last_error_code", "terminal_reason", "retry_after", "max_attempts",
                 "attempt", "lease_expires_at", "lease_owner", "heartbeat_at"):
        op.drop_column("shadow_trade_analysis_jobs", name)
    for table_name in ("profile_suggestions", "profile_ai_reviews", "shadow_trade_analysis_jobs"):
        op.drop_index(f"ix_{table_name}_tenant_ai_request", table_name=table_name)
        op.drop_column(table_name, "ai_request_id")
        op.drop_column(table_name, "tenant_id")
    for table_name in ("causal_evidence_refs", "mutation_fingerprints", "context_fingerprints", "decision_memory",
                       "experiment_links", "regeneration_runs", "ai_change_sets", "decision_hypotheses"):
        op.drop_table(table_name)
    op.drop_table("ai_tool_call_audits")
    op.drop_table("ai_budget_policies")
    op.drop_table("ai_usage_records")
    op.drop_table("ai_results")
    op.drop_table("ai_jobs")
    op.drop_table("ai_requests")
    op.drop_table("ai_dataset_snapshots")
    op.drop_table("ai_configuration_bundles")
    op.drop_table("ai_model_resolutions")
    op.drop_table("ai_model_aliases")
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_ai_prompt_immutable ON ai_prompt_versions"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_approved_ai_prompt_mutation()"))
    op.drop_table("ai_prompt_versions")
