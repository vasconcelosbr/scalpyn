"""Trade Monitor (Module 3) — add outcome columns to trade_tracking and decisions_log.

Revision ID: 041_trade_monitor
Revises: 040_reconciliation_fixes
Create Date: 2026-05-05

Context
-------
Trade Monitor (Module 3) closes open ``trade_tracking`` rows when
TP / SL / timeout conditions are met and back-fills the matching
``decisions_log`` row so the ML dataset has complete outcome information.

New columns:

trade_tracking
  exit_price     NUMERIC(20, 8) — price at which the trade was closed
  exit_time      TIMESTAMPTZ    — wall-clock time of the close
  outcome        VARCHAR(20)    — 'tp', 'sl', or 'timeout'
  pnl_pct        NUMERIC(10, 4) — percentage P&L at close
  holding_seconds INTEGER       — seconds between entry_time and exit_time

decisions_log
  outcome        VARCHAR(20)    — mirrored from trade_tracking
  pnl_pct        DOUBLE PRECISION
  holding_seconds INTEGER
"""

from alembic import op
import sqlalchemy as sa

revision = "041_trade_monitor"
down_revision = "040_reconciliation_fixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. trade_tracking — close / outcome columns ───────────────────────────
    op.execute(sa.text("""
        ALTER TABLE trade_tracking
            ADD COLUMN IF NOT EXISTS exit_price       NUMERIC(20, 8),
            ADD COLUMN IF NOT EXISTS exit_time        TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS outcome          VARCHAR(20),
            ADD COLUMN IF NOT EXISTS pnl_pct          NUMERIC(10, 4),
            ADD COLUMN IF NOT EXISTS holding_seconds  INTEGER
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_tracking_outcome
            ON trade_tracking (outcome)
            WHERE outcome IS NOT NULL
    """))

    # ── 2. decisions_log — outcome columns mirrored from trade_tracking ───────
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            ADD COLUMN IF NOT EXISTS outcome          VARCHAR(20),
            ADD COLUMN IF NOT EXISTS pnl_pct          DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS holding_seconds  INTEGER
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_decisions_log_outcome
            ON decisions_log (outcome)
            WHERE outcome IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_decisions_log_outcome"))
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            DROP COLUMN IF EXISTS outcome,
            DROP COLUMN IF EXISTS pnl_pct,
            DROP COLUMN IF EXISTS holding_seconds
    """))

    op.execute(sa.text("DROP INDEX IF EXISTS idx_trade_tracking_outcome"))
    op.execute(sa.text("""
        ALTER TABLE trade_tracking
            DROP COLUMN IF EXISTS exit_price,
            DROP COLUMN IF EXISTS exit_time,
            DROP COLUMN IF EXISTS outcome,
            DROP COLUMN IF EXISTS pnl_pct,
            DROP COLUMN IF EXISTS holding_seconds
    """))
