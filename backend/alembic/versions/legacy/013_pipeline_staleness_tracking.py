"""Add staleness tracking columns to pipeline watchlists

Revision ID: 013_pipeline_staleness_tracking
Revises: 012_pipeline_scan
Create Date: 2026-04-17

Adds:
  - pipeline_watchlists.last_scanned_at: tracks when the pipeline scan
    last successfully processed this watchlist.
  - pipeline_watchlist_assets.refreshed_at: tracks when the asset was last
    re-confirmed by a pipeline scan (staleness detection).
"""

from alembic import op
import sqlalchemy as sa

revision = "013_pipeline_staleness_tracking"
down_revision = "012_pipeline_scan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            -- pipeline_watchlists.last_scanned_at
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlists'
                  AND column_name = 'last_scanned_at'
            ) THEN
                ALTER TABLE pipeline_watchlists
                    ADD COLUMN last_scanned_at TIMESTAMPTZ;
            END IF;

            -- pipeline_watchlist_assets.refreshed_at
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'refreshed_at'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets
                    ADD COLUMN refreshed_at TIMESTAMPTZ;
            END IF;
        END $$;
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE pipeline_watchlist_assets DROP COLUMN IF EXISTS refreshed_at;
        ALTER TABLE pipeline_watchlists DROP COLUMN IF EXISTS last_scanned_at;
    """))
