"""Add explicit graph failure state and auditable AI budget reservations.

Revision ID: 156_intelligence_run_intents
Revises: 155_ai_analysis_profiles
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "156_intelligence_run_intents"
down_revision = "155_ai_analysis_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_graph_runs", sa.Column("last_completed_node", sa.String(160), nullable=True))
    op.add_column("ai_graph_runs", sa.Column("failed_node", sa.String(160), nullable=True))
    op.add_column("ai_graph_runs", sa.Column("error_kind", sa.String(80), nullable=True))
    op.add_column("ai_graph_runs", sa.Column("provider_transport_attempted", sa.Boolean(), nullable=True))

    op.create_table(
        "ai_budget_reservations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ai_request_id", UUID(as_uuid=True), sa.ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("budget_policy_id", UUID(as_uuid=True), sa.ForeignKey("ai_budget_policies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("model_approval_id", UUID(as_uuid=True), sa.ForeignKey("ai_model_approvals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_intent", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("module", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("request_token_limit", sa.Integer(), nullable=False),
        sa.Column("daily_token_limit", sa.Integer(), nullable=False),
        sa.Column("monthly_token_limit", sa.Integer(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_cost_usd", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("actual_tokens", sa.Integer(), nullable=True),
        sa.Column("actual_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("released_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overage_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("provider_transport_attempted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("terminal_reason", sa.String(160), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("transport_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("ai_request_id", name="uq_ai_budget_reservation_request"),
        sa.CheckConstraint(
            "request_intent IN ('NORMAL_ANALYSIS', 'FAKE_PROVIDER_CANARY', 'REAL_PROVIDER_CANARY')",
            name="ck_ai_budget_reservation_intent",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'TRANSPORT_STARTED', 'RECONCILED', 'RELEASED', 'TRANSPORT_ERROR')",
            name="ck_ai_budget_reservation_status",
        ),
        sa.CheckConstraint(
            "estimated_input_tokens >= 0 AND max_output_tokens >= 0 AND reserved_tokens >= 0",
            name="ck_ai_budget_reservation_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_cost_usd >= 0 AND (actual_cost_usd IS NULL OR actual_cost_usd >= 0)",
            name="ck_ai_budget_reservation_cost_nonnegative",
        ),
    )
    op.create_index(
        "ix_ai_budget_reservation_tenant_created",
        "ai_budget_reservations",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_ai_budget_reservation_status_updated",
        "ai_budget_reservations",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_budget_reservation_status_updated", table_name="ai_budget_reservations")
    op.drop_index("ix_ai_budget_reservation_tenant_created", table_name="ai_budget_reservations")
    op.drop_table("ai_budget_reservations")
    op.drop_column("ai_graph_runs", "provider_transport_attempted")
    op.drop_column("ai_graph_runs", "error_kind")
    op.drop_column("ai_graph_runs", "failed_node")
    op.drop_column("ai_graph_runs", "last_completed_node")
