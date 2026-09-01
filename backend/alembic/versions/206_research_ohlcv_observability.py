"""Add research OHLCV ingestion and readiness observations.

Revision ID: 206_research_ohlcv_observability
Revises: 205_l3_rejected_consolidation
"""

from alembic import op
import sqlalchemy as sa


revision = "206_research_ohlcv_observability"
down_revision = "205_l3_rejected_consolidation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ohlcv_ingestion_observations (
            observed_at TIMESTAMPTZ NOT NULL,
            symbol VARCHAR(30) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            source VARCHAR(30) NOT NULL,
            latest_open_time TIMESTAMPTZ NULL,
            close_time TIMESTAMPTZ NULL,
            availability_lag_seconds NUMERIC NULL,
            received_rows INTEGER NOT NULL,
            inserted_rows INTEGER NOT NULL,
            rejected_open_candles INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL,
            error_code VARCHAR(100) NULL,
            CONSTRAINT pk_ohlcv_ingestion_observations
                PRIMARY KEY (observed_at, symbol, timeframe),
            CONSTRAINT ck_ohlcv_ingestion_observations_timeframe
                CHECK (timeframe IN ('15m', '1h'))
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ohlcv_ingestion_observations_tf_observed
            ON ohlcv_ingestion_observations (timeframe, observed_at)
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ohlcv_readiness_snapshots (
            observed_at TIMESTAMPTZ NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            target_candles INTEGER NOT NULL,
            target_symbols INTEGER NOT NULL,
            present_symbols INTEGER NOT NULL,
            target_ready_symbols INTEGER NOT NULL,
            ema200_ready_symbols INTEGER NOT NULL,
            total_gap_candles BIGINT NOT NULL,
            minimum_rows INTEGER NOT NULL,
            median_rows NUMERIC NOT NULL,
            maximum_rows INTEGER NOT NULL,
            median_close_lag_seconds NUMERIC NULL,
            p95_close_lag_seconds NUMERIC NULL,
            CONSTRAINT pk_ohlcv_readiness_snapshots
                PRIMARY KEY (observed_at, timeframe),
            CONSTRAINT ck_ohlcv_readiness_snapshots_timeframe
                CHECK (timeframe IN ('15m', '1h'))
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ohlcv_readiness_snapshots_tf_observed
            ON ohlcv_readiness_snapshots (timeframe, observed_at)
    """))


def downgrade() -> None:
    op.drop_index(
        "ix_ohlcv_readiness_snapshots_tf_observed",
        table_name="ohlcv_readiness_snapshots",
    )
    op.drop_table("ohlcv_readiness_snapshots")
    op.drop_index(
        "ix_ohlcv_ingestion_observations_tf_observed",
        table_name="ohlcv_ingestion_observations",
    )
    op.drop_table("ohlcv_ingestion_observations")
