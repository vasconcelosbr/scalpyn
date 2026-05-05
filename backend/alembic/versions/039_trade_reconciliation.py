"""Add trade reconciliation support: external_id on trade_tracking + reconciled_gate_trades.

Revision ID: 039_trade_reconciliation
Revises: 038
Create Date: 2026-05-05

Context
-------
Trade Reconciliation (Module 2) needs two schema changes:

1.  ``trade_tracking.external_id`` — nullable VARCHAR(100) column that the
    reconciliation service populates when a Gate.io trade fill is matched to
    (or creates) a trade_tracking row.  Exposed as a dedup handle: if a row
    already has an external_id we know it was already reconciled.

2.  ``reconciled_gate_trades`` — lightweight dedup registry.  One row per
    Gate trade fill (external_id + market_type) processed by the reconciler,
    regardless of whether a trade_tracking match was found.  The composite
    unique index (external_id, market_type) prevents double-processing even
    if the Celery task fires more than once inside the same 30-60 s window.
"""

from alembic import op
import sqlalchemy as sa

revision = "039_trade_reconciliation"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add external_id to trade_tracking ─────────────────────────────────
    op.execute(sa.text("""
        ALTER TABLE trade_tracking
            ADD COLUMN IF NOT EXISTS external_id VARCHAR(100)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_tracking_external_id
            ON trade_tracking (external_id)
            WHERE external_id IS NOT NULL
    """))

    # ── 2. Create reconciled_gate_trades dedup table ──────────────────────────
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS reconciled_gate_trades (
            id                  BIGSERIAL PRIMARY KEY,
            external_id         VARCHAR(100) NOT NULL,
            market_type         VARCHAR(10)  NOT NULL,
            trade_tracking_id   UUID         REFERENCES trade_tracking (id) ON DELETE SET NULL,
            processed_at        TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT uq_reconciled_gate_trades_ext_market
                UNIQUE (external_id, market_type)
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_reconciled_gate_trades_processed_at
            ON reconciled_gate_trades (processed_at DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS reconciled_gate_trades CASCADE"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_trade_tracking_external_id"))
    op.execute(sa.text("""
        ALTER TABLE trade_tracking
            DROP COLUMN IF EXISTS external_id
    """))
