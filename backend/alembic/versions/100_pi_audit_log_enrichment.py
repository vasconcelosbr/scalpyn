"""Enrich profile_intelligence_audit_log with before/after/diff snapshots and actor context.

Revision ID: 100_pi_audit_log_enrichment
Revises: 099_autopilot_audit_enrichment
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

revision = "100_pi_audit_log_enrichment"
down_revision = "099_autopilot_audit_enrichment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_intelligence_audit_log",
        sa.Column("before_json", JSONB, nullable=True),
    )
    op.add_column(
        "profile_intelligence_audit_log",
        sa.Column("after_json", JSONB, nullable=True),
    )
    op.add_column(
        "profile_intelligence_audit_log",
        sa.Column("diff_json", JSONB, nullable=True),
    )
    op.add_column(
        "profile_intelligence_audit_log",
        sa.Column("actor_user_id", PG_UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "profile_intelligence_audit_log",
        sa.Column("profile_name", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "profile_intelligence_audit_log",
        sa.Column("source_run_id", PG_UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "idx_pi_audit_actor",
        "profile_intelligence_audit_log",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "idx_pi_audit_source_run",
        "profile_intelligence_audit_log",
        ["source_run_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_pi_audit_source_run", table_name="profile_intelligence_audit_log")
    op.drop_index("idx_pi_audit_actor", table_name="profile_intelligence_audit_log")
    op.drop_column("profile_intelligence_audit_log", "source_run_id")
    op.drop_column("profile_intelligence_audit_log", "profile_name")
    op.drop_column("profile_intelligence_audit_log", "actor_user_id")
    op.drop_column("profile_intelligence_audit_log", "diff_json")
    op.drop_column("profile_intelligence_audit_log", "after_json")
    op.drop_column("profile_intelligence_audit_log", "before_json")
