"""Defensive catch-all: ensure futures-mode columns from Task #28 are present

Revision ID: 020_pipeline_futures_columns
Revises: 019_pipeline_columns_catchall
Create Date: 2026-04-26

Adds (idempotently) every column that Task #28 (Futures Mode) introduced via
init_db() but never via Alembic. Without these columns, ORM SELECTs against
pipeline_watchlists / pipeline_watchlist_assets fail with
`column "market_mode" does not exist`, causing /api/watchlists to 500 and the
PipelineTab UI to silently render empty.

Strategy mirrors migration 019 (pipeline_columns_catchall): use IF NOT EXISTS
inside a DO $$ block so the migration is fully idempotent and safe to run
even on databases where init_db() already added the columns.
"""

from alembic import op
import sqlalchemy as sa

revision = "020_pipeline_futures_columns"
down_revision = "019_pipeline_columns_catchall"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            -- pipeline_watchlists.market_mode (default 'spot')
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlists'
                  AND column_name = 'market_mode'
            ) THEN
                ALTER TABLE pipeline_watchlists
                    ADD COLUMN market_mode VARCHAR(10) NOT NULL DEFAULT 'spot';
            END IF;

            -- pipeline_watchlist_assets futures-scoring columns
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'score_long'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN score_long NUMERIC(5,2);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'score_short'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN score_short NUMERIC(5,2);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'confidence_score'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN confidence_score NUMERIC(5,2);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'futures_direction'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN futures_direction VARCHAR(10);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'entry_long_blocked'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets
                    ADD COLUMN entry_long_blocked BOOLEAN NOT NULL DEFAULT FALSE;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'entry_short_blocked'
            ) THEN
                ALTER TABLE pipeline_watchlist_assets
                    ADD COLUMN entry_short_blocked BOOLEAN NOT NULL DEFAULT FALSE;
            END IF;
        END $$;
    """))


def downgrade() -> None:
    # These columns may have been added by init_db() too — do not drop.
    pass
