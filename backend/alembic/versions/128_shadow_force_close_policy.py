"""Configure shadow force-close policy.

Revision ID: 128_shadow_force_close
Revises: 127_shadow_fs_immutable
Create Date: 2026-07-03
"""

from alembic import op
from sqlalchemy import text


revision = "128_shadow_force_close"
down_revision = "127_shadow_fs_immutable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        UPDATE config_profiles
           SET config_json = config_json
               || jsonb_build_object(
                    'shadow_max_open_age_hours',
                    COALESCE(config_json->'shadow_max_open_age_hours', '18'::jsonb),
                    'shadow_force_close_policy',
                    COALESCE(
                        config_json->'shadow_force_close_policy',
                        '"TIMEOUT_LAST_KNOWN_PRICE"'::jsonb
                    )
                  ),
               updated_at = now()
         WHERE config_type = 'ml'
           AND is_active = true
    """))


def downgrade() -> None:
    op.execute(text("""
        UPDATE config_profiles
           SET config_json = config_json
                              - 'shadow_max_open_age_hours'
                              - 'shadow_force_close_policy',
               updated_at = now()
         WHERE config_type = 'ml'
           AND is_active = true
    """))
