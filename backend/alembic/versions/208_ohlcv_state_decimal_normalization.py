"""Version the exact-decimal OHLCV shadow capture contract.

Revision ID: 208_ohlcv_decimal_norm
Revises: 207_ohlcv_state_dual_run
"""

from alembic import op
import sqlalchemy as sa


revision = "208_ohlcv_decimal_norm"
down_revision = "207_ohlcv_state_dual_run"
branch_labels = None
depends_on = None


CAPTURE_CONTRACT_VERSION = "gate_ohlcv_state_v2"


def upgrade() -> None:
    # The future frontier gives the API/migration and isolated worker enough
    # time to deploy before any candle is admitted to the comparison cohort.
    op.execute(sa.text("""
        INSERT INTO ohlcv_capture_contracts
            (capture_contract_version, valid_from, mode, source, timeframes,
             closed_table, live_table, canonical_read_enabled)
        VALUES
            (:version, clock_timestamp() + INTERVAL '5 minutes',
             'SHADOW', 'gate.io', '["1m", "5m", "30m"]'::jsonb,
             'ohlcv_shadow', 'ohlcv_live', FALSE)
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
