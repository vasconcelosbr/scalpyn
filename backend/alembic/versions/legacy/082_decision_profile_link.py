"""decisions_log.profile_id — link decisions to the profile that generated them

Adds profile attribution to the decisions_log table. All existing rows remain valid
(profile_id IS NULL = legacy/global decision, not attributed to a specific profile).

Revision ID: 082_decision_profile_link
Revises: 081_profile_type_versioning
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "082_decision_profile_link"
down_revision = "081_profile_type_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns — nullable for full backward compatibility
    op.execute(sa.text("""
        ALTER TABLE decisions_log
        ADD COLUMN IF NOT EXISTS profile_id      UUID        NULL
            REFERENCES profiles(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS profile_name    VARCHAR(255) NULL,
        ADD COLUMN IF NOT EXISTS profile_version TIMESTAMPTZ  NULL
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_decisions_profile_created
        ON decisions_log (user_id, profile_id, created_at DESC)
        WHERE profile_id IS NOT NULL
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_decisions_profile_id
        ON decisions_log (profile_id, created_at DESC)
        WHERE profile_id IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_decisions_profile_created"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_decisions_profile_id"))
    op.execute(sa.text("""
        ALTER TABLE decisions_log
        DROP COLUMN IF EXISTS profile_id,
        DROP COLUMN IF EXISTS profile_name,
        DROP COLUMN IF EXISTS profile_version
    """))
