"""Trade Reconciliation Module 2 — critical schema fixes.

Revision ID: 040_reconciliation_fixes
Revises: 039_trade_reconciliation
Create Date: 2026-05-05

Context
-------
Four critical adjustments to the Trade Reconciliation schema:

1.  ``trade_tracking.real_entry_price`` — captures the actual Gate.io fill
    price while preserving the original ``entry_price`` (decision/signal
    price) for slippage analysis.  ``real_entry_price − entry_price =
    slippage``.

2.  ``decisions_log.trade_executed``,
    ``decisions_log.execution_type``,
    ``decisions_log.execution_entry_price``,
    ``decisions_log.execution_entry_time`` — dedicated columns written by
    the reconciliation service instead of patching the immutable
    ``decisions_log.metrics`` JSONB blob.  The metrics column must remain
    read-only after pipeline_scan writes it.
"""

from alembic import op
import sqlalchemy as sa

revision = "040_reconciliation_fixes"
down_revision = "039_trade_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add real_entry_price to trade_tracking ─────────────────────────────
    op.execute(sa.text("""
        ALTER TABLE trade_tracking
            ADD COLUMN IF NOT EXISTS real_entry_price NUMERIC(20, 8)
    """))

    # ── 2. Add execution tracking columns to decisions_log ────────────────────
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            ADD COLUMN IF NOT EXISTS trade_executed        BOOLEAN,
            ADD COLUMN IF NOT EXISTS execution_type        VARCHAR(10),
            ADD COLUMN IF NOT EXISTS execution_entry_price DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS execution_entry_time  TIMESTAMPTZ
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_decisions_log_trade_executed
            ON decisions_log (trade_executed)
            WHERE trade_executed = TRUE
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_decisions_log_trade_executed"))
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            DROP COLUMN IF EXISTS trade_executed,
            DROP COLUMN IF EXISTS execution_type,
            DROP COLUMN IF EXISTS execution_entry_price,
            DROP COLUMN IF EXISTS execution_entry_time
    """))
    op.execute(sa.text("""
        ALTER TABLE trade_tracking
            DROP COLUMN IF EXISTS real_entry_price
    """))
