"""Pump Monitor 5-minute order-book history (v1.18).

Additive only: the last order-book snapshot of each 5-minute bucket (spread,
±1 % depth, imbalance, estimated slippage), with ``observed_at`` (cycle time) and
``computed_at`` (receipt), kept for ``flow_history.retention_days``.

Revision ID: 241_pump_book_5m
Revises: 240_pump_unfreeze_candle
"""
from alembic import op
import sqlalchemy as sa

revision = "241_pump_book_5m"
down_revision = "240_pump_unfreeze_candle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pump_book_5m (
            symbol varchar(40) NOT NULL,
            bucket_start timestamptz NOT NULL,
            observed_at timestamptz NOT NULL,
            spread_pct double precision,
            bid_depth_1pct_usdt double precision,
            ask_depth_1pct_usdt double precision,
            depth_imbalance_1pct double precision,
            slippage_buy_pct double precision,
            slippage_sell_pct double precision,
            computed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, bucket_start)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pump_book_5m_bucket ON pump_book_5m (bucket_start)"))


def downgrade():
    op.execute(sa.text("DROP TABLE IF EXISTS pump_book_5m"))
