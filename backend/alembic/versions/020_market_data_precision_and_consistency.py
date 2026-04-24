"""Increase market-data volume precision.

Revision ID: 020_market_data_precision_and_consistency
Revises: 019_pipeline_columns_catchall
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


revision = "020_market_data_precision_and_consistency"
down_revision = "019_pipeline_columns_catchall"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE ohlcv
            ALTER COLUMN volume TYPE NUMERIC(20, 8),
            ALTER COLUMN quote_volume TYPE NUMERIC(20, 8);
    """))
    op.execute(sa.text("""
        ALTER TABLE market_metadata
            ALTER COLUMN volume_24h TYPE NUMERIC(20, 8);
    """))
    op.execute(sa.text("""
        ALTER TABLE pipeline_watchlist_assets
            ALTER COLUMN volume_24h TYPE NUMERIC(20, 8);
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE pipeline_watchlist_assets
            ALTER COLUMN volume_24h TYPE NUMERIC(20, 2);
    """))
    op.execute(sa.text("""
        ALTER TABLE market_metadata
            ALTER COLUMN volume_24h TYPE NUMERIC(20, 2);
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv
            ALTER COLUMN quote_volume TYPE NUMERIC(20, 4),
            ALTER COLUMN volume TYPE NUMERIC(20, 4);
    """))
