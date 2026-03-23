"""Add profile_role, pipeline_order, auto_pilot and preset_ia fields to profiles

Revision ID: 009_profile_role_autopilot
Revises: 008_fix_id_defaults
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "009_profile_role_autopilot"
down_revision = "008_fix_id_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            ALTER TABLE profiles ADD COLUMN IF NOT EXISTS profile_role VARCHAR(50);
            ALTER TABLE profiles ADD COLUMN IF NOT EXISTS pipeline_order VARCHAR(3) NOT NULL DEFAULT '99';
            ALTER TABLE profiles ADD COLUMN IF NOT EXISTS pipeline_label VARCHAR(100);
            ALTER TABLE profiles ADD COLUMN IF NOT EXISTS auto_pilot_enabled BOOLEAN NOT NULL DEFAULT false;
            ALTER TABLE profiles ADD COLUMN IF NOT EXISTS auto_pilot_config JSONB NOT NULL DEFAULT '{}';
            ALTER TABLE profiles ADD COLUMN IF NOT EXISTS preset_ia_last_run TIMESTAMPTZ;
            ALTER TABLE profiles ADD COLUMN IF NOT EXISTS preset_ia_config JSONB;
        END $$;
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_profiles_role_order
            ON profiles (profile_role, pipeline_order);
    """))

    # Auto-assign roles based on existing profile names
    op.execute(sa.text("""
        UPDATE profiles SET
            profile_role   = CASE
                WHEN UPPER(name) LIKE '%L1%'     OR UPPER(name) LIKE '%FILTER%'  THEN 'primary_filter'
                WHEN UPPER(name) LIKE '%L2%'     OR UPPER(name) LIKE '%SCORE%'   THEN 'score_engine'
                WHEN UPPER(name) LIKE '%L3%'     OR UPPER(name) LIKE '%SIGNAL%'  THEN 'acquisition_queue'
                WHEN UPPER(name) LIKE '%POOL%'   OR UPPER(name) LIKE '%UNIVERSE%' THEN 'universe_filter'
                ELSE NULL
            END,
            pipeline_order = CASE
                WHEN UPPER(name) LIKE '%L1%'     OR UPPER(name) LIKE '%FILTER%'  THEN '1'
                WHEN UPPER(name) LIKE '%L2%'     OR UPPER(name) LIKE '%SCORE%'   THEN '2'
                WHEN UPPER(name) LIKE '%L3%'     OR UPPER(name) LIKE '%SIGNAL%'  THEN '3'
                WHEN UPPER(name) LIKE '%POOL%'   OR UPPER(name) LIKE '%UNIVERSE%' THEN '0'
                ELSE '99'
            END
        WHERE profile_role IS NULL;
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_profiles_role_order;"))
    for col in ["profile_role", "pipeline_order", "pipeline_label",
                "auto_pilot_enabled", "auto_pilot_config",
                "preset_ia_last_run", "preset_ia_config"]:
        op.execute(sa.text(f"ALTER TABLE profiles DROP COLUMN IF EXISTS {col};"))
