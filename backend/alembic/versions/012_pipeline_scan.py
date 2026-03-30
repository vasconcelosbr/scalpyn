"""Pipeline watchlist tables + unique constraint for upsert

Revision ID: 012_pipeline_scan
Revises: 011_autopilot_pool
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "012_pipeline_scan"
down_revision = "011_autopilot_pool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            -- Create pipeline_watchlists if not exists
            CREATE TABLE IF NOT EXISTS pipeline_watchlists (
                id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name                VARCHAR(100) NOT NULL,
                level               VARCHAR(10)  NOT NULL DEFAULT 'L1',
                source_pool_id      UUID REFERENCES pools(id) ON DELETE SET NULL,
                source_watchlist_id UUID REFERENCES pipeline_watchlists(id) ON DELETE SET NULL,
                profile_id          UUID REFERENCES profiles(id) ON DELETE SET NULL,
                auto_refresh        BOOLEAN NOT NULL DEFAULT TRUE,
                filters_json        JSONB    NOT NULL DEFAULT '{}',
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            -- Create pipeline_watchlist_assets if not exists
            CREATE TABLE IF NOT EXISTS pipeline_watchlist_assets (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                watchlist_id     UUID NOT NULL REFERENCES pipeline_watchlists(id) ON DELETE CASCADE,
                symbol           VARCHAR(20) NOT NULL,
                current_price    NUMERIC(20, 8),
                price_change_24h NUMERIC(8, 4),
                volume_24h       NUMERIC(20, 2),
                market_cap       NUMERIC(20, 2),
                alpha_score      NUMERIC(5, 2),
                entered_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                previous_level   VARCHAR(10),
                level_change_at  TIMESTAMPTZ,
                level_direction  VARCHAR(4)
            );

            -- Unique constraint required for ON CONFLICT upsert in pipeline_scan task
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
        DROP TABLE IF EXISTS pipeline_watchlist_assets;
        DROP TABLE IF EXISTS pipeline_watchlists;
    """))
