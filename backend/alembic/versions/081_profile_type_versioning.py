"""profile_type and versioning fields on profiles table

Adds classification and temporal versioning support needed by Profile Intelligence.

profile_type values:
  STANDARD   — regular user-created profile (default for all existing rows)
  LAB        — Strategy Lab profile (L3_*_V3 naming convention)
  AUTOPILOT  — managed by auto-pilot engine
  META       — meta-profile / ensemble
  GENERATED  — created automatically by intelligence engine

profile_version — timestamp of last config change; bumped on every config edit.
generated_by    — identifier of the engine that created this profile (NULL = human).
is_shadow_only  — TRUE = never triggers live trades (used by intelligence engine).
live_trading_enabled — FALSE for all rows; never set to TRUE by any migration.

Revision ID: 081_profile_type_versioning
Revises: 080_opportunity_snapshots
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "081_profile_type_versioning"
down_revision = "080_opportunity_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE profiles
        ADD COLUMN IF NOT EXISTS profile_type          VARCHAR(20)  NOT NULL DEFAULT 'STANDARD',
        ADD COLUMN IF NOT EXISTS profile_version       TIMESTAMPTZ  NULL,
        ADD COLUMN IF NOT EXISTS generated_by          VARCHAR(100) NULL,
        ADD COLUMN IF NOT EXISTS generated_from_suggestion_id UUID NULL,
        ADD COLUMN IF NOT EXISTS is_shadow_only        BOOLEAN      NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS live_trading_enabled  BOOLEAN      NOT NULL DEFAULT FALSE
    """))

    # Backfill profile_version = updated_at for existing rows
    op.execute(sa.text("""
        UPDATE profiles
        SET profile_version = updated_at
        WHERE profile_version IS NULL
    """))

    # Index for querying by type
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_profiles_type
        ON profiles (profile_type)
        WHERE is_active = true
    """))

    # Guard: live_trading_enabled must never be set to TRUE by a migration.
    # The constraint is informational — actual guard is the DEFAULT FALSE above
    # and the update_profile endpoint which ignores live_trading_enabled from payload.


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_profiles_type"))
    op.execute(sa.text("""
        ALTER TABLE profiles
        DROP COLUMN IF EXISTS profile_type,
        DROP COLUMN IF EXISTS profile_version,
        DROP COLUMN IF EXISTS generated_by,
        DROP COLUMN IF EXISTS generated_from_suggestion_id,
        DROP COLUMN IF EXISTS is_shadow_only,
        DROP COLUMN IF EXISTS live_trading_enabled
    """))
