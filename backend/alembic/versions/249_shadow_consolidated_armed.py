"""Shadow simulation provenance, independent of real order authorization.

Revision ID: 249_shadow_consolidated_armed
Revises: 248_mtf_history
"""
from alembic import op
import sqlalchemy as sa

revision = "249_shadow_consolidated_armed"
down_revision = "248_mtf_history"
branch_labels = None
depends_on = None


def upgrade():
    # No FK: history retention must not erase or prevent retaining a Shadow.
    op.execute(sa.text("ALTER TABLE shadow_trades ADD COLUMN IF NOT EXISTS mtf_observation_id uuid"))
    op.execute(sa.text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_mtf_observation
        ON shadow_trades(user_id, mtf_observation_id) WHERE mtf_observation_id IS NOT NULL"""))
    op.execute(sa.text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_consolidated_active
        ON shadow_trades(user_id,symbol,direction)
        WHERE source='L3_CONSOLIDATED' AND status IN ('PENDING','RUNNING')"""))


def downgrade():
    op.execute(sa.text("DROP INDEX IF EXISTS uq_shadow_consolidated_active"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_shadow_mtf_observation"))
    op.execute(sa.text("ALTER TABLE shadow_trades DROP COLUMN IF EXISTS mtf_observation_id"))
