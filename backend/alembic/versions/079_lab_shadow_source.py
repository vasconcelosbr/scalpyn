"""Strategy Lab shadow source: L3_LAB distinct from L3

Revision ID: 079_lab_shadow_source
Revises: 078_ml_models_profile
Create Date: 2026-06-17

- Narrows ux_shadow_running_user_source to canonical shadows only (profile_id IS NULL).
  Lab shadows (profile_id IS NOT NULL) are deduped by uq_shadow_lab_profile_symbol_bucket
  and must not be blocked by the running-position constraint — multiple profiles can shadow
  the same symbol simultaneously.
- Existing lab shadow rows remain unaffected (index is partial, data unchanged).
"""

from alembic import op
import sqlalchemy as sa

revision = "079_lab_shadow_source"
down_revision = "078_ml_models_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ux_shadow_running_user_source"))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX ux_shadow_running_user_source
        ON shadow_trades (user_id, symbol, source)
        WHERE status = 'RUNNING' AND profile_id IS NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ux_shadow_running_user_source"))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX ux_shadow_running_user_source
        ON shadow_trades (user_id, symbol, source)
        WHERE status = 'RUNNING'
    """))
