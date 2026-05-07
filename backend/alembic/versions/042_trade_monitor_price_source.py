"""Trade Monitor (Module 3) — add exit_price_source to trade_tracking.

Revision ID: 042_trade_monitor_price_source
Revises: 041_trade_monitor
Create Date: 2026-05-05

Context
-------
The Trade Monitor closes trades using the Gate.io public ticker price,
which is an *estimated* exit price — not the actual exchange fill price.

``exit_price_source`` records which authority provided the exit price:

  * ``'market'``   — Gate.io public ticker (estimated; used by the monitor)
  * ``'exchange'`` — actual fill price from the exchange (future: reconciliation
                     confirms the real close price)

This lets the ML layer and analytics distinguish between exact PnL (exchange
fills) and approximate PnL (ticker-based closes), and prepares the schema for
future reconciliation of real close fills without a second migration.
"""

from alembic import op
import sqlalchemy as sa

revision = "042_trade_monitor_price_source"
down_revision = "041_trade_monitor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE trade_tracking
            ADD COLUMN IF NOT EXISTS exit_price_source VARCHAR(20)
    """))

    # Partial index — only closed rows carry a source value.
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_tracking_exit_price_source
            ON trade_tracking (exit_price_source)
            WHERE exit_price_source IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_trade_tracking_exit_price_source"))
    op.execute(sa.text("""
        ALTER TABLE trade_tracking
            DROP COLUMN IF EXISTS exit_price_source
    """))
