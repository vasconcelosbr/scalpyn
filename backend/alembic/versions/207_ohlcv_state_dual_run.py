"""Add closed/live OHLCV state tables for the governed dual run.

Revision ID: 207_ohlcv_state_dual_run
Revises: 206_research_ohlcv_observability
"""

from alembic import op
import sqlalchemy as sa


revision = "207_ohlcv_state_dual_run"
down_revision = "206_research_ohlcv_observability"
branch_labels = None
depends_on = None


CAPTURE_CONTRACT_VERSION = "gate_ohlcv_state_v1"


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ohlcv_capture_contracts (
            capture_contract_version VARCHAR(80) PRIMARY KEY,
            valid_from TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            mode VARCHAR(20) NOT NULL,
            source VARCHAR(30) NOT NULL,
            timeframes JSONB NOT NULL,
            closed_table VARCHAR(63) NOT NULL,
            live_table VARCHAR(63) NOT NULL,
            canonical_read_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT ck_ohlcv_capture_contract_mode
                CHECK (mode IN ('SHADOW', 'CANONICAL', 'RETIRED')),
            CONSTRAINT ck_ohlcv_capture_contract_shadow_read
                CHECK (mode <> 'SHADOW' OR canonical_read_enabled IS FALSE)
        )
    """))
    op.execute(sa.text("""
        INSERT INTO ohlcv_capture_contracts
            (capture_contract_version, mode, source, timeframes,
             closed_table, live_table, canonical_read_enabled)
        VALUES
            (:version, 'SHADOW', 'gate.io', '["1m", "5m", "30m"]'::jsonb,
             'ohlcv_shadow', 'ohlcv_live', FALSE)
        ON CONFLICT (capture_contract_version) DO NOTHING
    """).bindparams(version=CAPTURE_CONTRACT_VERSION))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ohlcv_shadow (
            time TIMESTAMPTZ NOT NULL,
            symbol VARCHAR(20) NOT NULL,
            exchange VARCHAR(50) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            market_type VARCHAR(10) NOT NULL DEFAULT 'spot',
            open NUMERIC(20, 8) NOT NULL,
            high NUMERIC(20, 8) NOT NULL,
            low NUMERIC(20, 8) NOT NULL,
            close NUMERIC(20, 8) NOT NULL,
            volume NUMERIC(20, 4) NOT NULL,
            quote_volume NUMERIC(20, 4) NOT NULL,
            is_closed BOOLEAN NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            capture_contract_version VARCHAR(80) NOT NULL,
            CONSTRAINT pk_ohlcv_shadow
                PRIMARY KEY (time, symbol, exchange, timeframe),
            CONSTRAINT ck_ohlcv_shadow_closed CHECK (is_closed IS TRUE),
            CONSTRAINT ck_ohlcv_shadow_timeframe
                CHECK (timeframe IN ('1m', '5m', '30m')),
            CONSTRAINT fk_ohlcv_shadow_capture_contract
                FOREIGN KEY (capture_contract_version)
                REFERENCES ohlcv_capture_contracts(capture_contract_version)
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ohlcv_shadow_tf_symbol_time
            ON ohlcv_shadow (timeframe, symbol, time DESC)
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ohlcv_live (
            time TIMESTAMPTZ NOT NULL,
            symbol VARCHAR(20) NOT NULL,
            exchange VARCHAR(50) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            market_type VARCHAR(10) NOT NULL DEFAULT 'spot',
            open NUMERIC(20, 8) NOT NULL,
            high NUMERIC(20, 8) NOT NULL,
            low NUMERIC(20, 8) NOT NULL,
            close NUMERIC(20, 8) NOT NULL,
            volume NUMERIC(20, 4) NOT NULL,
            quote_volume NUMERIC(20, 4) NOT NULL,
            is_closed BOOLEAN NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            capture_contract_version VARCHAR(80) NOT NULL,
            CONSTRAINT pk_ohlcv_live
                PRIMARY KEY (time, symbol, exchange, timeframe),
            CONSTRAINT ck_ohlcv_live_open CHECK (is_closed IS FALSE),
            CONSTRAINT ck_ohlcv_live_timeframe
                CHECK (timeframe IN ('1m', '5m', '30m')),
            CONSTRAINT fk_ohlcv_live_capture_contract
                FOREIGN KEY (capture_contract_version)
                REFERENCES ohlcv_capture_contracts(capture_contract_version)
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ohlcv_live_tf_symbol_time
            ON ohlcv_live (timeframe, symbol, time DESC)
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ohlcv_state_ingestion_observations (
            observed_at TIMESTAMPTZ NOT NULL,
            symbol VARCHAR(30) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            source VARCHAR(30) NOT NULL,
            capture_contract_version VARCHAR(80) NOT NULL,
            latest_closed_open_time TIMESTAMPTZ NULL,
            latest_closed_close_time TIMESTAMPTZ NULL,
            availability_lag_seconds NUMERIC NULL,
            received_rows INTEGER NOT NULL,
            inserted_closed_rows INTEGER NOT NULL,
            upserted_live_rows INTEGER NOT NULL,
            rejected_from_closed_rows INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL,
            error_code VARCHAR(100) NULL,
            CONSTRAINT pk_ohlcv_state_ingestion_observations
                PRIMARY KEY (observed_at, symbol, timeframe,
                             capture_contract_version),
            CONSTRAINT ck_ohlcv_state_ingestion_timeframe
                CHECK (timeframe IN ('1m', '5m', '30m')),
            CONSTRAINT fk_ohlcv_state_ingestion_contract
                FOREIGN KEY (capture_contract_version)
                REFERENCES ohlcv_capture_contracts(capture_contract_version)
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ohlcv_state_ingestion_tf_observed
            ON ohlcv_state_ingestion_observations
               (timeframe, observed_at DESC)
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ohlcv_capture_comparison_snapshots (
            observed_at TIMESTAMPTZ NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            capture_contract_version VARCHAR(80) NOT NULL,
            valid_from TIMESTAMPTZ NOT NULL,
            shadow_rows BIGINT NOT NULL,
            canonical_rows BIGINT NOT NULL,
            compared_rows BIGINT NOT NULL,
            exact_rows BIGINT NOT NULL,
            divergent_rows BIGINT NOT NULL,
            missing_canonical_rows BIGINT NOT NULL,
            median_close_lag_seconds NUMERIC NULL,
            p95_close_lag_seconds NUMERIC NULL,
            CONSTRAINT pk_ohlcv_capture_comparison_snapshots
                PRIMARY KEY (observed_at, timeframe,
                             capture_contract_version),
            CONSTRAINT ck_ohlcv_capture_comparison_timeframe
                CHECK (timeframe IN ('1m', '5m', '30m')),
            CONSTRAINT ck_ohlcv_capture_comparison_partition
                CHECK (compared_rows = exact_rows + divergent_rows),
            CONSTRAINT fk_ohlcv_capture_comparison_contract
                FOREIGN KEY (capture_contract_version)
                REFERENCES ohlcv_capture_contracts(capture_contract_version)
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ohlcv_capture_comparison_tf_observed
            ON ohlcv_capture_comparison_snapshots
               (timeframe, observed_at DESC)
    """))


def downgrade() -> None:
    op.drop_index(
        "ix_ohlcv_capture_comparison_tf_observed",
        table_name="ohlcv_capture_comparison_snapshots",
    )
    op.drop_table("ohlcv_capture_comparison_snapshots")
    op.drop_index(
        "ix_ohlcv_state_ingestion_tf_observed",
        table_name="ohlcv_state_ingestion_observations",
    )
    op.drop_table("ohlcv_state_ingestion_observations")
    op.drop_index("ix_ohlcv_live_tf_symbol_time", table_name="ohlcv_live")
    op.drop_table("ohlcv_live")
    op.drop_index("ix_ohlcv_shadow_tf_symbol_time", table_name="ohlcv_shadow")
    op.drop_table("ohlcv_shadow")
    op.drop_table("ohlcv_capture_contracts")
