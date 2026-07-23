"""Add Profile Intelligence AI v2 contract and model audit.

Revision ID: 139_pi_ai_v2
Revises: 138_profile_score_optimization
Create Date: 2026-07-23

Additive only: this migration does not touch shadow trades, ML datasets,
profiles, score-engine versions, model registry, or promotion state.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "139_pi_ai_v2"
down_revision = "138_profile_score_optimization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_score_optimization_runs",
        sa.Column("analysis_contract_version", sa.String(64), nullable=True),
    )
    op.add_column(
        "profile_score_optimization_runs",
        sa.Column("analysis_skill_version", sa.String(120), nullable=True),
    )
    op.add_column(
        "profile_score_optimization_runs",
        sa.Column("ai_model_requested", sa.String(120), nullable=True),
    )
    op.add_column(
        "profile_score_optimization_runs",
        sa.Column("ai_model_effective", sa.String(120), nullable=True),
    )
    op.create_table(
        "profile_intelligence_ai_model_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_model", sa.String(120), nullable=True),
        sa.Column("new_model", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(160), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("capabilities", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["config_id"], ["config_profiles.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_pi_ai_model_audit_user_created",
        "profile_intelligence_ai_model_audit",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pi_ai_model_audit_user_created",
        table_name="profile_intelligence_ai_model_audit",
    )
    op.drop_table("profile_intelligence_ai_model_audit")
    op.drop_column("profile_score_optimization_runs", "ai_model_effective")
    op.drop_column("profile_score_optimization_runs", "ai_model_requested")
    op.drop_column("profile_score_optimization_runs", "analysis_skill_version")
    op.drop_column("profile_score_optimization_runs", "analysis_contract_version")
