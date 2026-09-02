"""Add Gate settlement-latency sampling table for R1.B.

Revision ID: 210_ohlcv_settle_latency
Revises: 209_ohlcv_settle_grace
"""

from alembic import op
import sqlalchemy as sa


revision = "210_ohlcv_settle_latency"
down_revision = "209_ohlcv_settle_grace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ohlcv_settlement_latency_samples (
            symbol VARCHAR(20) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            candle_open_time TIMESTAMPTZ NOT NULL,
            candle_close_time TIMESTAMPTZ NOT NULL,
            delay_target_seconds INTEGER NOT NULL,
            delay_actual_seconds NUMERIC NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            found BOOLEAN NOT NULL,
            open NUMERIC(24, 8) NULL,
            high NUMERIC(24, 8) NULL,
            low NUMERIC(24, 8) NULL,
            close NUMERIC(24, 8) NULL,
            volume NUMERIC(24, 8) NULL,
            quote_volume NUMERIC(24, 8) NULL,
            is_closed BOOLEAN NULL,
            CONSTRAINT pk_ohlcv_settlement_latency_samples
                PRIMARY KEY (symbol, timeframe, candle_open_time,
                             delay_target_seconds),
            CONSTRAINT ck_ohlcv_settlement_latency_timeframe
                CHECK (timeframe IN ('1m', '5m', '30m')),
            CONSTRAINT ck_ohlcv_settlement_latency_delay
                CHECK (delay_target_seconds IN (10, 30, 60, 120, 300))
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ohlcv_settlement_latency_candle
            ON ohlcv_settlement_latency_samples
               (symbol, timeframe, candle_open_time)
    """))


def downgrade() -> None:
    op.drop_index(
        "ix_ohlcv_settlement_latency_candle",
        table_name="ohlcv_settlement_latency_samples",
    )
    op.drop_table("ohlcv_settlement_latency_samples")
