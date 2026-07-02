"""Profile Intelligence operational Co-Pilot.

Revision ID: 126_profile_intelligence_copilot
Revises: 125_shadow_profile_lineage
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "126_profile_intelligence_copilot"
down_revision = "125_shadow_profile_lineage"
branch_labels = None
depends_on = None


def _uuid(name, *args, **kwargs):
    return sa.Column(name, postgresql.UUID(as_uuid=True), *args, **kwargs)


def upgrade() -> None:
    op.create_table(
        "copilot_sessions",
        _uuid("id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid("user_id", sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("context", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
    )
    op.create_index("idx_copilot_sessions_user_started", "copilot_sessions", ["user_id", "started_at"])

    op.create_table(
        "copilot_messages",
        _uuid("id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid("session_id", sa.ForeignKey("copilot_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_copilot_messages_session_created", "copilot_messages", ["session_id", "created_at"])

    op.create_table(
        "copilot_query_runs",
        _uuid("id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid("user_id", sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        _uuid("session_id", sa.ForeignKey("copilot_sessions.id", ondelete="SET NULL")),
        sa.Column("query_text", sa.Text, nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("query_type", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("parameters", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("rows_returned", sa.Integer),
        sa.Column("execution_ms", sa.Integer),
        sa.Column("result_preview", postgresql.JSONB),
        sa.Column("result_truncated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_copilot_query_runs_user_created", "copilot_query_runs", ["user_id", "created_at"])
    op.create_index("idx_copilot_query_runs_session", "copilot_query_runs", ["session_id", "created_at"])

    op.create_table(
        "copilot_action_plans",
        _uuid("id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid("user_id", sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        _uuid("session_id", sa.ForeignKey("copilot_sessions.id", ondelete="SET NULL")),
        sa.Column("action_type", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(60), nullable=False),
        sa.Column("target_id", sa.String(100)),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("evidence", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("proposed_diff", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("execution_payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("risk_assessment", sa.Text),
        sa.Column("rollback_plan", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("target_state_hash", sa.String(64)),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRY_RUN"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True)),
        _uuid("approved_by", sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approval_text", sa.String(80)),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("execution_result", postgresql.JSONB),
    )
    op.create_index("idx_copilot_actions_user_status", "copilot_action_plans", ["user_id", "status", "created_at"])

    op.create_table(
        "copilot_skills",
        _uuid("id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid("user_id", sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("skill_type", sa.String(50), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="ACTIVE"),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("source", sa.String(160)),
        sa.Column("requires_approval", sa.Boolean, nullable=False, server_default=sa.false()),
        _uuid("approved_by", sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name", "version", name="uq_copilot_skill_user_name_version"),
    )
    op.create_index("idx_copilot_skills_retrieval", "copilot_skills", ["user_id", "status", "skill_type"])

    op.create_table(
        "copilot_audit_logs",
        _uuid("id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid("user_id", sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        _uuid("session_id", sa.ForeignKey("copilot_sessions.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(80), nullable=False),
        _uuid("actor_user_id", sa.ForeignKey("users.id", ondelete="SET NULL")),
        _uuid("action_plan_id", sa.ForeignKey("copilot_action_plans.id", ondelete="SET NULL")),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_copilot_audit_user_created", "copilot_audit_logs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("copilot_audit_logs")
    op.drop_table("copilot_skills")
    op.drop_table("copilot_action_plans")
    op.drop_table("copilot_query_runs")
    op.drop_table("copilot_messages")
    op.drop_table("copilot_sessions")
