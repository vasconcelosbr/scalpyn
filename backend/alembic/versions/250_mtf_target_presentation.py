"""Seed the visual target without changing engine or user preferences.

Revision ID: 250_mtf_target_presentation
Revises: 249_shadow_consolidated_armed
"""
from alembic import op
import sqlalchemy as sa

revision = '250_mtf_target_presentation'
down_revision = '249_shadow_consolidated_armed'
branch_labels = None
depends_on = None


def upgrade():
    # config_json is JSONB. Missing/incomplete presentations remain unconfigured.
    op.execute(sa.text("""UPDATE config_profiles
        SET config_json = jsonb_set(config_json, '{presentation,target_pct}', '1'::jsonb)
        WHERE config_type='mtf_analysis'
          AND jsonb_typeof(config_json->'presentation')='object'
          AND NOT (config_json->'presentation' ? 'target_pct')"""))


def downgrade():
    # Remove only the introduced visual key; preserve every other user setting.
    op.execute(sa.text("""UPDATE config_profiles
        SET config_json = config_json #- '{presentation,target_pct}'
        WHERE config_type='mtf_analysis'
          AND jsonb_typeof(config_json->'presentation')='object'"""))
