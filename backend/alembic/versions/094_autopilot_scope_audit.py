"""Add mandatory scope and mutation evidence to legacy autopilot audit.

Revision ID: 094_autopilot_scope_audit
Revises: 093_pi_autopilot
Create Date: 2026-06-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "094_autopilot_scope_audit"
down_revision = "093_pi_autopilot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "autopilot_audit_logs",
        "action",
        existing_type=sa.String(length=30),
        type_=sa.String(length=80),
        existing_nullable=False,
    )
    op.alter_column(
        "autopilot_audit_logs",
        "profile_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("reason_code", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("target_config", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("target_section", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("performance_window", JSONB(), nullable=True),
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("evidence_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("diff_json", JSONB(), nullable=True),
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column(
            "mutation_applied",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_foreign_key(
        "fk_autopilot_audit_logs_user_id",
        "autopilot_audit_logs",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_autopilot_audit_logs_user_id",
        "autopilot_audit_logs",
        type_="foreignkey",
    )
    op.drop_column("autopilot_audit_logs", "mutation_applied")
    op.drop_column("autopilot_audit_logs", "diff_json")
    op.drop_column("autopilot_audit_logs", "evidence_count")
    op.drop_column("autopilot_audit_logs", "performance_window")
    op.drop_column("autopilot_audit_logs", "target_section")
    op.drop_column("autopilot_audit_logs", "target_config")
    op.drop_column("autopilot_audit_logs", "reason_code")
    op.drop_column("autopilot_audit_logs", "user_id")
    op.alter_column(
        "autopilot_audit_logs",
        "profile_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "autopilot_audit_logs",
        "action",
        existing_type=sa.String(length=80),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
