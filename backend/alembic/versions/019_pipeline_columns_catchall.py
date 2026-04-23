"""Defensive catch-all: ensure all pipeline columns added by 013/017/018 are present

Revision ID: 019_pipeline_columns_catchall
Revises: 018_pipeline_analysis_snapshots
Create Date: 2026-04-23

Adds (idempotently) every column that migrations 013, 017, and 018 were supposed
to add.  Those migrations used non-defensive op.add_column() calls (without
IF NOT EXISTS), which caused DuplicateColumnError when init_db() backfilled the
same columns first.  This migration ensures the DB is in a consistent state
regardless of which path created the tables.
"""

from alembic import op
import sqlalchemy as sa

revision = "019_pipeline_columns_catchall"
down_revision = "018_pipeline_analysis_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            -- migration 013: staleness tracking
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlists'
                  AND column_name = 'last_scanned_at'
            ) THEN
                ALTER TABLE pipeline_watchlists ADD COLUMN last_scanned_at TIMESTAMPTZ;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'refreshed_at'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN refreshed_at TIMESTAMPTZ;
            END IF;

            -- migration 017: execution_id tracing
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'execution_id'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN execution_id UUID;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_rejections'
                  AND column_name = 'execution_id'
            ) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD COLUMN execution_id UUID;
            END IF;

            -- migration 018: analysis snapshots
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'analysis_snapshot'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN analysis_snapshot JSONB;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_rejections'
                  AND column_name = 'analysis_snapshot'
            ) THEN
                ALTER TABLE pipeline_watchlist_rejections ADD COLUMN analysis_snapshot JSONB;
            END IF;
        END $$;
    """))


def downgrade() -> None:
    # These columns were added by earlier migrations; do not drop them here.
    pass
