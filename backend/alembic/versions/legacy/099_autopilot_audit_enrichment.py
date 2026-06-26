"""Enrich autopilot_audit_logs with trigger_source, task_id and profile_name.

Revision ID: 099_autopilot_audit_enrichment
Revises: 098_forward_autonomy_policy
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa


revision = "099_autopilot_audit_enrichment"
down_revision = "098_forward_autonomy_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("trigger_source", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "autopilot_audit_logs",
        sa.Column("profile_name", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_autopilot_audit_logs_trigger_source",
        "autopilot_audit_logs",
        ["trigger_source"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_autopilot_audit_logs_trigger_source",
        table_name="autopilot_audit_logs",
    )
    op.drop_column("autopilot_audit_logs", "profile_name")
    op.drop_column("autopilot_audit_logs", "celery_task_id")
    op.drop_column("autopilot_audit_logs", "trigger_source")
