"""Add trigger_source to profile_intelligence_runs.

Revision ID: 100_pi_run_trigger_source
Revises: 099_autopilot_audit_enrichment
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa


revision = "100_pi_run_trigger_source"
down_revision = "099_autopilot_audit_enrichment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_intelligence_runs",
        sa.Column("trigger_source", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "ix_pi_runs_trigger_source",
        "profile_intelligence_runs",
        ["trigger_source"],
    )


def downgrade() -> None:
    op.drop_index("ix_pi_runs_trigger_source", table_name="profile_intelligence_runs")
    op.drop_column("profile_intelligence_runs", "trigger_source")
