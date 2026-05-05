"""Add trade_tracking table and decisions_log.processed flag.

Revision ID: 038
Revises: 037
Create Date: 2026-05-05

Context
-------
Decision Log Enricher (Module 1) needs:

1.  ``decisions_log.processed`` — boolean flag so the enricher can mark
    a decision after spawning a trade_tracking row and avoid re-processing.
2.  ``trade_tracking`` — lightweight open-trade record created from every
    ALLOW decision.  It is intentionally *not* a real executed trade; it
    stores simulated / potential entry metadata for downstream modules.

The ``trade_tracking.entry_price`` is read from
``decisions_log.metrics->>'price'`` at enrichment time.  If that field
is absent (legacy rows), the enricher skips the decision gracefully.
"""

from alembic import op
import sqlalchemy as sa

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add processed flag to decisions_log ───────────────────────────────
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            ADD COLUMN IF NOT EXISTS processed BOOLEAN NOT NULL DEFAULT FALSE
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_decisions_log_processed
            ON decisions_log (processed)
            WHERE processed = FALSE
    """))

    # ── 2. Create trade_tracking table ───────────────────────────────────────
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS trade_tracking (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            decision_id     BIGINT,
            symbol          VARCHAR(20)  NOT NULL,
            market_type     VARCHAR(10)  NOT NULL DEFAULT 'spot',
            position_side   VARCHAR(10)  NOT NULL DEFAULT 'long',

            is_simulated    BOOLEAN      NOT NULL DEFAULT TRUE,

            entry_price     NUMERIC(20, 8) NOT NULL,
            entry_time      TIMESTAMPTZ    NOT NULL,

            target_price    NUMERIC(20, 8),
            stop_price      NUMERIC(20, 8),

            status          VARCHAR(20)  NOT NULL DEFAULT 'open',

            created_at      TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT fk_trade_tracking_decision_id
                FOREIGN KEY (decision_id)
                REFERENCES decisions_log (id)
                ON DELETE SET NULL
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_tracking_symbol
            ON trade_tracking (symbol)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_tracking_status
            ON trade_tracking (status)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_tracking_decision_id
            ON trade_tracking (decision_id)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_tracking_created_at
            ON trade_tracking (created_at DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS trade_tracking CASCADE"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_decisions_log_processed"))
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            DROP COLUMN IF EXISTS processed
    """))
