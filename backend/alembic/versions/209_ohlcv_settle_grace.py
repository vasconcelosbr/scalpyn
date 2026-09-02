"""Add a versioned Gate settlement grace before immutable persistence.

Revision ID: 209_ohlcv_settle_grace
Revises: 208_ohlcv_decimal_norm
"""

from alembic import op
import sqlalchemy as sa


revision = "209_ohlcv_settle_grace"
down_revision = "208_ohlcv_decimal_norm"
branch_labels = None
depends_on = None


CAPTURE_CONTRACT_VERSION = "gate_ohlcv_state_v3"


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE ohlcv_capture_contracts
        ADD COLUMN IF NOT EXISTS finalization_delay_seconds INTEGER NULL
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv_capture_contracts
        DROP CONSTRAINT IF EXISTS ck_ohlcv_capture_contract_finalization_delay
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv_capture_contracts
        ADD CONSTRAINT ck_ohlcv_capture_contract_finalization_delay
        CHECK (
            finalization_delay_seconds IS NULL
            OR finalization_delay_seconds BETWEEN 0 AND 600
        )
    """))
    op.execute(sa.text("""
        INSERT INTO ohlcv_capture_contracts
            (capture_contract_version, valid_from, mode, source, timeframes,
             closed_table, live_table, canonical_read_enabled,
             finalization_delay_seconds)
        VALUES
            (:version, clock_timestamp() + INTERVAL '5 minutes',
             'SHADOW', 'gate.io', '["1m", "5m", "30m"]'::jsonb,
             'ohlcv_shadow', 'ohlcv_live', FALSE, 60)
        ON CONFLICT (capture_contract_version) DO NOTHING
    """).bindparams(version=CAPTURE_CONTRACT_VERSION))


def downgrade() -> None:
    op.execute(
        sa.text("""
            DELETE FROM ohlcv_capture_contracts
             WHERE capture_contract_version = :version
               AND NOT EXISTS (
                   SELECT 1 FROM ohlcv_shadow
                    WHERE capture_contract_version = :version
               )
               AND NOT EXISTS (
                   SELECT 1 FROM ohlcv_live
                    WHERE capture_contract_version = :version
               )
               AND NOT EXISTS (
                   SELECT 1 FROM ohlcv_state_ingestion_observations
                    WHERE capture_contract_version = :version
               )
               AND NOT EXISTS (
                   SELECT 1 FROM ohlcv_capture_comparison_snapshots
                    WHERE capture_contract_version = :version
               )
        """).bindparams(version=CAPTURE_CONTRACT_VERSION)
    )
    op.execute(sa.text("""
        ALTER TABLE ohlcv_capture_contracts
        DROP CONSTRAINT IF EXISTS ck_ohlcv_capture_contract_finalization_delay
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv_capture_contracts
        DROP COLUMN IF EXISTS finalization_delay_seconds
    """))
