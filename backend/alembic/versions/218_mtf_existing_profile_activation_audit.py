"""Persist rollback snapshots for governed existing-profile MTF activation.

Revision ID: 218_mtf_profile_activation_audit
Revises: 217_indicator_identity_idx
"""

from alembic import op
import sqlalchemy as sa


revision = "218_mtf_profile_activation_audit"
down_revision = "217_indicator_identity_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS mtf_profile_activation_audits (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
          calibration_run_id UUID NOT NULL
            REFERENCES mtf_calibration_runs(id) ON DELETE RESTRICT,
          applied_by UUID REFERENCES users(id) ON DELETE SET NULL,
          import_mode VARCHAR(80) NOT NULL,
          document_hash VARCHAR(64) NOT NULL,
          before_snapshot JSONB NOT NULL,
          after_snapshot JSONB NOT NULL,
          status VARCHAR(20) NOT NULL,
          rolled_back_by UUID REFERENCES users(id) ON DELETE SET NULL,
          rolled_back_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CONSTRAINT ck_mtf_profile_activation_before_object
            CHECK (jsonb_typeof(before_snapshot) = 'object'),
          CONSTRAINT ck_mtf_profile_activation_after_object
            CHECK (jsonb_typeof(after_snapshot) = 'object'),
          CONSTRAINT ck_mtf_profile_activation_status
            CHECK (status IN ('APPLIED', 'ROLLED_BACK')),
          CONSTRAINT ck_mtf_profile_activation_rollback_fields
            CHECK (
              (status = 'APPLIED' AND rolled_back_at IS NULL)
              OR (status = 'ROLLED_BACK' AND rolled_back_at IS NOT NULL)
            )
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_mtf_profile_activation_user_created
          ON mtf_profile_activation_audits (user_id, created_at DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS mtf_profile_activation_audits"))
