"""Add Gate-final 1m replay table for the trailing-policy cohort study.

New and separate population for the frozen 559-manifest cohort
(docs/audits/r1/r1a_cohort_559_manifest.json). Never written by any
production task; batch-populated once by an offline research script and
read only by offline analysis. Does not touch ohlcv, ohlcv_shadow or
ohlcv_live.

Revision ID: 211_shadow_trailing_replay
Revises: 210_ohlcv_settle_latency
"""

from alembic import op
import sqlalchemy as sa


revision = "211_shadow_trailing_replay"
down_revision = "210_ohlcv_settle_latency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_trailing_replay_candles_1m (
            symbol VARCHAR(20) NOT NULL,
            time TIMESTAMPTZ NOT NULL,
            open NUMERIC(24, 8) NOT NULL,
            high NUMERIC(24, 8) NOT NULL,
            low NUMERIC(24, 8) NOT NULL,
            close NUMERIC(24, 8) NOT NULL,
            volume NUMERIC(24, 8) NOT NULL,
            quote_volume NUMERIC(24, 8) NOT NULL,
            is_closed BOOLEAN NOT NULL DEFAULT TRUE,
            source VARCHAR(20) NOT NULL DEFAULT 'gate.io',
            replay_contract_version VARCHAR(60) NOT NULL,
            cohort_manifest_sha256 VARCHAR(64) NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT pk_shadow_trailing_replay_candles_1m
                PRIMARY KEY (symbol, time),
            CONSTRAINT ck_shadow_trailing_replay_is_closed
                CHECK (is_closed IS TRUE)
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trailing_replay_symbol_time
            ON shadow_trailing_replay_candles_1m (symbol, time)
    """))


def downgrade() -> None:
    op.drop_index(
        "ix_shadow_trailing_replay_symbol_time",
        table_name="shadow_trailing_replay_candles_1m",
    )
    op.drop_table("shadow_trailing_replay_candles_1m")
