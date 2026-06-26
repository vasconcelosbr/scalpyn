"""Add missing unique constraint on pipeline_watchlist_assets(watchlist_id, symbol).

Revision ID: 066_pipeline_assets_uq
Revises: 065_ttt_shadow_columns
Create Date: 2026-06-09

Context: When Railway DB was bootstrapped via init_db + alembic stamp 020,
migration 012 (which creates uq_pipeline_asset_watchlist_symbol) was skipped.
The ON CONFLICT upsert in pipeline_scan._upsert_assets requires this constraint
— without it every pipeline_scan cycle fails with InvalidColumnReferenceError,
leaving shadow_trades empty.

Note: revision ID intentionally short (≤32 chars) to fit alembic_version.version_num VARCHAR(32).
"""

from alembic import op
import sqlalchemy as sa

revision = "066_pipeline_assets_uq"
down_revision = "065_ttt_shadow_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM   pg_constraint
                WHERE  conname = 'uq_pipeline_asset_watchlist_symbol'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets
                    ADD CONSTRAINT uq_pipeline_asset_watchlist_symbol
                    UNIQUE (watchlist_id, symbol);
            END IF;
        END $$;
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE pipeline_watchlist_assets
            DROP CONSTRAINT IF EXISTS uq_pipeline_asset_watchlist_symbol;
    """))
