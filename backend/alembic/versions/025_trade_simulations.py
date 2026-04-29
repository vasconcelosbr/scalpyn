"""Add trade_simulations table for ML dataset generation

Revision ID: 025_trade_simulations
Revises: 024_ohlcv_backfill_constraints
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "025_trade_simulations"
down_revision = "024_ohlcv_backfill_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS trade_simulations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            symbol VARCHAR(20) NOT NULL,
            timestamp_entry TIMESTAMPTZ NOT NULL,
            entry_price NUMERIC(20, 8) NOT NULL,

            tp_price NUMERIC(20, 8) NOT NULL,
            sl_price NUMERIC(20, 8) NOT NULL,

            exit_price NUMERIC(20, 8),
            exit_timestamp TIMESTAMPTZ,

            result VARCHAR(10) NOT NULL CHECK (result IN ('WIN', 'LOSS', 'TIMEOUT')),
            time_to_result INTEGER,

            direction VARCHAR(10) NOT NULL CHECK (direction IN ('LONG', 'SHORT', 'SPOT')),

            is_simulated BOOLEAN DEFAULT TRUE,
            source VARCHAR(30) DEFAULT 'SIMULATION',

            decision_type VARCHAR(10) NOT NULL CHECK (decision_type IN ('ALLOW', 'BLOCK')),
            decision_id BIGINT,

            features_snapshot JSONB,
            config_snapshot JSONB,

            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT uq_simulation_symbol_entry_direction
                UNIQUE (symbol, timestamp_entry, direction)
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_symbol
            ON trade_simulations(symbol)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_timestamp_entry
            ON trade_simulations(timestamp_entry)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_result
            ON trade_simulations(result)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_direction
            ON trade_simulations(direction)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_decision_type
            ON trade_simulations(decision_type)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_symbol_timestamp
            ON trade_simulations(symbol, timestamp_entry DESC)
    """))

    # Optional FK to decisions_log — only added when the table exists
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'decisions_log'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'fk_trade_simulations_decision_id'
            ) THEN
                ALTER TABLE trade_simulations
                    ADD CONSTRAINT fk_trade_simulations_decision_id
                    FOREIGN KEY (decision_id) REFERENCES decisions_log(id)
                    ON DELETE SET NULL;
            END IF;
        END $$
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS trade_simulations CASCADE"))
