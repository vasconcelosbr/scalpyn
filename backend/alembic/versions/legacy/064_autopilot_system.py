"""Create autopilot_audit_logs and profile_versions tables

Revision ID: 064_autopilot_system
Revises: 063_shadow_timeout_post_analysis
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "064_autopilot_system"
down_revision = "063_shadow_timeout_post_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── profile_versions — config snapshots for rollback ──────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_versions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            profile_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            version_number  INTEGER NOT NULL,
            config          JSONB NOT NULL DEFAULT '{}',
            regime          VARCHAR(30),
            ev_at_snapshot  NUMERIC(8, 4),
            win_rate_at_snapshot NUMERIC(6, 4),
            fpr_at_snapshot NUMERIC(6, 4),
            n_samples       INTEGER,
            mutation_reason TEXT,
            is_active       BOOLEAN NOT NULL DEFAULT false,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_profile_versions_profile_id
            ON profile_versions (profile_id, version_number DESC);
    """))

    # ── autopilot_audit_logs — full decision audit trail ──────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS autopilot_audit_logs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            profile_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            action          VARCHAR(30) NOT NULL,
            reason          TEXT,
            regime          VARCHAR(30),
            perf_snapshot   JSONB,
            config_before   JSONB,
            config_after    JSONB,
            version_id      UUID REFERENCES profile_versions(id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_autopilot_audit_logs_profile_id
            ON autopilot_audit_logs (profile_id, created_at DESC);
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS autopilot_audit_logs;"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_versions;"))
