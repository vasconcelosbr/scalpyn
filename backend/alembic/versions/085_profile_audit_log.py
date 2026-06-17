"""profile_audit_log — immutable log of changes to profiles.config

Every time profiles.config changes, a row is inserted here with previous and new
config snapshots. Also tracks profile_version transitions.

This table is append-only. No rows are ever deleted or updated.

Revision ID: 085_profile_audit_log
Revises: 084_config_profiles_unique
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "085_profile_audit_log"
down_revision = "084_config_profiles_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_audit_log (
            id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                 UUID        NOT NULL,
            profile_id              UUID        NOT NULL
                REFERENCES profiles(id) ON DELETE CASCADE,
            changed_by              UUID        NULL
                REFERENCES users(id) ON DELETE SET NULL,
            change_source           VARCHAR(50) NULL,
            change_description      TEXT        NULL,
            previous_config         JSONB       NULL,
            new_config              JSONB       NULL,
            previous_profile_version TIMESTAMPTZ NULL,
            new_profile_version     TIMESTAMPTZ NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_profile_audit_profile_created
        ON profile_audit_log (user_id, profile_id, created_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_profile_audit_profile_id
        ON profile_audit_log (profile_id, created_at DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS profile_audit_log"))
