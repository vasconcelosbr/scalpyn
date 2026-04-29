"""OHLCV backfill constraints and indexes for performance

Revision ID: 024_ohlcv_backfill_constraints
Revises: 023_taker_ratio_scale_v2
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa

revision = "024_ohlcv_backfill_constraints"
down_revision = "023_taker_ratio_scale_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            -- Add unique constraint for ON CONFLICT DO NOTHING (idempotency)
            IF NOT EXISTS (
                SELECT 1
                FROM   pg_constraint
                WHERE  conname = 'uq_ohlcv_time_symbol_exchange_timeframe'
            ) THEN
                ALTER TABLE ohlcv
                    ADD CONSTRAINT uq_ohlcv_time_symbol_exchange_timeframe
                    UNIQUE (time, symbol, exchange, timeframe);
            END IF;

            -- Index for backfill queries (checking earliest/latest timestamps)
            IF NOT EXISTS (
                SELECT 1
                FROM   pg_indexes
                WHERE  indexname = 'idx_ohlcv_symbol_exchange_timeframe_time'
            ) THEN
                CREATE INDEX idx_ohlcv_symbol_exchange_timeframe_time
                ON ohlcv (symbol, exchange, timeframe, time DESC);
            END IF;

            -- Index for time-based queries (used by pipeline/indicators)
            IF NOT EXISTS (
                SELECT 1
                FROM   pg_indexes
                WHERE  indexname = 'idx_ohlcv_timeframe_symbol_time'
            ) THEN
                CREATE INDEX idx_ohlcv_timeframe_symbol_time
                ON ohlcv (timeframe, symbol, time DESC);
            END IF;

            -- Enable TimescaleDB compression if hypertable exists
            -- Compress chunks older than 7 days to save storage
            IF EXISTS (
                SELECT 1
                FROM timescaledb_information.hypertables
                WHERE hypertable_name = 'ohlcv'
            ) THEN
                -- Enable compression if not already enabled
                IF NOT EXISTS (
                    SELECT 1
                    FROM timescaledb_information.compression_settings
                    WHERE hypertable_name = 'ohlcv'
                ) THEN
                    ALTER TABLE ohlcv SET (
                        timescaledb.compress,
                        timescaledb.compress_segmentby = 'symbol, exchange, timeframe',
                        timescaledb.compress_orderby = 'time DESC'
                    );
                END IF;

                -- Add compression policy if not exists
                IF NOT EXISTS (
                    SELECT 1
                    FROM timescaledb_information.jobs
                    WHERE proc_name = 'policy_compression'
                      AND hypertable_name = 'ohlcv'
                ) THEN
                    SELECT add_compression_policy('ohlcv', INTERVAL '7 days');
                END IF;

                -- Add retention policy (keep 1 year of data)
                IF NOT EXISTS (
                    SELECT 1
                    FROM timescaledb_information.jobs
                    WHERE proc_name = 'policy_retention'
                      AND hypertable_name = 'ohlcv'
                ) THEN
                    SELECT add_retention_policy('ohlcv', INTERVAL '365 days');
                END IF;
            END IF;

        END $$;
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        -- Remove compression and retention policies if they exist
        DO $$
        DECLARE
            job_id INTEGER;
        BEGIN
            -- Drop compression policy
            SELECT job_id INTO job_id
            FROM timescaledb_information.jobs
            WHERE proc_name = 'policy_compression'
              AND hypertable_name = 'ohlcv';

            IF FOUND THEN
                PERFORM remove_compression_policy('ohlcv');
            END IF;

            -- Drop retention policy
            SELECT job_id INTO job_id
            FROM timescaledb_information.jobs
            WHERE proc_name = 'policy_retention'
              AND hypertable_name = 'ohlcv';

            IF FOUND THEN
                PERFORM remove_retention_policy('ohlcv');
            END IF;
        END $$;

        -- Drop indexes
        DROP INDEX IF EXISTS idx_ohlcv_symbol_exchange_timeframe_time;
        DROP INDEX IF EXISTS idx_ohlcv_timeframe_symbol_time;

        -- Drop unique constraint
        ALTER TABLE ohlcv
            DROP CONSTRAINT IF EXISTS uq_ohlcv_time_symbol_exchange_timeframe;
    """))
