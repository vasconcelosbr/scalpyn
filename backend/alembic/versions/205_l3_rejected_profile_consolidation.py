"""Add the canonical active-owner index for consolidated L3 rejects.

Revision ID: 205_l3_rejected_consolidation
Revises: 204_shadow_exit_measurement

Production rollout pre-creates this index with ``CONCURRENTLY``.  The
transactional Alembic step is intentionally idempotent and therefore becomes
a metadata acknowledgement when the online index already exists.
"""

from alembic import op
import sqlalchemy as sa


revision = "205_l3_rejected_consolidation"
down_revision = "204_shadow_exit_measurement"
branch_labels = None
depends_on = None


INDEX_NAME = "ux_shadow_l3_rejected_consolidated_active"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
                ON shadow_trades (user_id, symbol, direction)
             WHERE source = 'L3_REJECTED'
               AND l3_consolidation_enforced = TRUE
               AND status IN ('PENDING', 'RUNNING')
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
