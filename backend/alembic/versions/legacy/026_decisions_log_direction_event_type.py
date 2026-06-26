"""Add direction and event_type columns to decisions_log

Revision ID: 026_decisions_log_direction_event_type
Revises: 025_trade_simulations
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa

revision = "026_dl_direction_event_type"
down_revision = "025_trade_simulations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-statement cap; complements the session-wide lock_timeout in env.py.
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            ADD COLUMN IF NOT EXISTS direction VARCHAR(10),
            ADD COLUMN IF NOT EXISTS event_type VARCHAR(40)
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            DROP COLUMN IF EXISTS direction,
            DROP COLUMN IF EXISTS event_type
    """))
