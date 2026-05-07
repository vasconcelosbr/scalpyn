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
    # Step 1: constraints and indexes — no TimescaleDB dependency
    op.execute(sa.text("""
        DO $main$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_ohlcv_time_symbol_exchange_timeframe'
            ) THEN
                ALTER TABLE ohlcv
                    ADD CONSTRAINT uq_ohlcv_time_symbol_exchange_timeframe
                    UNIQUE (time, symbol, exchange, timeframe);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'idx_ohlcv_symbol_exchange_timeframe_time'
            ) THEN
                CREATE INDEX idx_ohlcv_symbol_exchange_timeframe_time
                    ON ohlcv (symbol, exchange, timeframe, time DESC);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'idx_ohlcv_timeframe_symbol_time'
            ) THEN
                CREATE INDEX idx_ohlcv_timeframe_symbol_time
                    ON ohlcv (timeframe, symbol, time DESC);
            END IF;
        END $main$;
    """))

    # Step 2: TimescaleDB compression/retention — skipped on plain PostgreSQL.
    # All timescaledb_information.* references are executed as dynamic SQL to
    # prevent parse-time failures when the TimescaleDB extension is absent.
    op.execute(sa.text("""
        DO $main$
        DECLARE
            _ts_installed   boolean;
            _is_hypertable  boolean := false;
            _has_compress   boolean := false;
            _has_cp         boolean := false;
            _has_rp         boolean := false;
        BEGIN
            SELECT EXISTS(
                SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
            ) INTO _ts_installed;

            IF NOT _ts_installed THEN
                RETURN;
            END IF;

            EXECUTE
                'SELECT EXISTS(SELECT 1 FROM timescaledb_information.hypertables'
                ' WHERE hypertable_name = ''ohlcv'')'
            INTO _is_hypertable;

            IF NOT _is_hypertable THEN
                RETURN;
            END IF;

            EXECUTE
                'SELECT EXISTS(SELECT 1 FROM timescaledb_information.compression_settings'
                ' WHERE hypertable_name = ''ohlcv'')'
            INTO _has_compress;

            IF NOT _has_compress THEN
                EXECUTE
                    'ALTER TABLE ohlcv SET ('
                    '    timescaledb.compress,'
                    '    timescaledb.compress_segmentby = ''symbol, exchange, timeframe'','
                    '    timescaledb.compress_orderby = ''time DESC'''
                    ')';
            END IF;

            EXECUTE
                'SELECT EXISTS(SELECT 1 FROM timescaledb_information.jobs'
                ' WHERE proc_name = ''policy_compression'''
                '   AND hypertable_name = ''ohlcv'')'
            INTO _has_cp;

            IF NOT _has_cp THEN
                EXECUTE 'SELECT add_compression_policy(''ohlcv'', INTERVAL ''7 days'')';
            END IF;

            EXECUTE
                'SELECT EXISTS(SELECT 1 FROM timescaledb_information.jobs'
                ' WHERE proc_name = ''policy_retention'''
                '   AND hypertable_name = ''ohlcv'')'
            INTO _has_rp;

            IF NOT _has_rp THEN
                EXECUTE 'SELECT add_retention_policy(''ohlcv'', INTERVAL ''365 days'')';
            END IF;
        END $main$;
    """))


def downgrade() -> None:
    # Remove TimescaleDB policies only when the extension is present
    op.execute(sa.text("""
        DO $main$
        DECLARE
            _ts_installed boolean;
            _has_cp       boolean := false;
            _has_rp       boolean := false;
        BEGIN
            SELECT EXISTS(
                SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
            ) INTO _ts_installed;

            IF NOT _ts_installed THEN
                RETURN;
            END IF;

            EXECUTE
                'SELECT EXISTS(SELECT 1 FROM timescaledb_information.jobs'
                ' WHERE proc_name = ''policy_compression'''
                '   AND hypertable_name = ''ohlcv'')'
            INTO _has_cp;

            IF _has_cp THEN
                EXECUTE 'SELECT remove_compression_policy(''ohlcv'')';
            END IF;

            EXECUTE
                'SELECT EXISTS(SELECT 1 FROM timescaledb_information.jobs'
                ' WHERE proc_name = ''policy_retention'''
                '   AND hypertable_name = ''ohlcv'')'
            INTO _has_rp;

            IF _has_rp THEN
                EXECUTE 'SELECT remove_retention_policy(''ohlcv'')';
            END IF;
        END $main$;
    """))

    op.execute(sa.text("""
        DROP INDEX IF EXISTS idx_ohlcv_symbol_exchange_timeframe_time;
        DROP INDEX IF EXISTS idx_ohlcv_timeframe_symbol_time;
        ALTER TABLE ohlcv
            DROP CONSTRAINT IF EXISTS uq_ohlcv_time_symbol_exchange_timeframe;
    """))
