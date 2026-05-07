"""Catch-all parity with init_db.py — eliminates schema drift forever

Revision ID: 021_init_db_parity_catchall
Revises: 020_pipeline_futures_columns
Create Date: 2026-04-26

Mirrors EVERY DDL currently in backend/app/init_db.py inside one idempotent
DO $$ block.  Historical context:

  Init_db.py has been the safety net for columns added to ORM models without
  matching Alembic migrations.  When Cloud Run's start.sh time-boxed the
  alembic upgrade and silenced init_db, deploys silently shipped with missing
  columns.  This caused two production incidents (Task #41: market_mode,
  follow-up: last_scanned_at) where /api/watchlists 500'd and the UI hid the
  failure.

  Going forward, start.sh runs alembic to completion AND init_db, both gating
  startup.  This migration guarantees that running `alembic upgrade head` on
  ANY production state — fully migrated, partially init_db-bootstrapped, or
  somewhere in between — converges to a known-good schema.

  All ALTER TABLE / CREATE TABLE / CREATE INDEX statements use IF NOT EXISTS
  guards.  Downgrade is intentionally empty: dropping these columns would
  break the live ORM.
"""

from alembic import op
import sqlalchemy as sa

revision = "021_init_db_parity_catchall"
down_revision = "020_pipeline_futures_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Tables that init_db.py creates with raw SQL ──────────────────────────
    # CREATE TABLE IF NOT EXISTS is natively idempotent.
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profiles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            config JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS watchlist_profiles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            watchlist_id VARCHAR(100) NOT NULL,
            profile_type VARCHAR(10) NOT NULL DEFAULT 'L2',
            profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
            is_enabled BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(user_id, watchlist_id, profile_type)
        );
    """))

    # ── Column parity (one big DO block — all guarded by IF NOT EXISTS) ──────
    op.execute(sa.text("""
        DO $$
        BEGIN
            -- ── pools (Pool ORM) ─────────────────────────────────────────
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pools' AND column_name = 'overrides') THEN
                ALTER TABLE pools ADD COLUMN overrides JSONB DEFAULT '{}';
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pools' AND column_name = 'autopilot_enabled') THEN
                ALTER TABLE pools ADD COLUMN autopilot_enabled BOOLEAN NOT NULL DEFAULT false;
            END IF;

            -- ── pipeline_watchlists ──────────────────────────────────────
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlists' AND column_name = 'market_mode') THEN
                ALTER TABLE pipeline_watchlists ADD COLUMN market_mode VARCHAR(10) NOT NULL DEFAULT 'spot';
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlists' AND column_name = 'last_scanned_at') THEN
                ALTER TABLE pipeline_watchlists ADD COLUMN last_scanned_at TIMESTAMPTZ;
            END IF;

            -- ── pipeline_watchlist_assets ────────────────────────────────
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_assets' AND column_name = 'execution_id') THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN execution_id UUID;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_assets' AND column_name = 'score_long') THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN score_long NUMERIC(5,2);
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_assets' AND column_name = 'score_short') THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN score_short NUMERIC(5,2);
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_assets' AND column_name = 'confidence_score') THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN confidence_score NUMERIC(5,2);
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_assets' AND column_name = 'futures_direction') THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN futures_direction VARCHAR(10);
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_assets' AND column_name = 'entry_long_blocked') THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN entry_long_blocked BOOLEAN NOT NULL DEFAULT FALSE;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_assets' AND column_name = 'entry_short_blocked') THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN entry_short_blocked BOOLEAN NOT NULL DEFAULT FALSE;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_assets' AND column_name = 'refreshed_at') THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN refreshed_at TIMESTAMPTZ;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_assets' AND column_name = 'analysis_snapshot') THEN
                ALTER TABLE pipeline_watchlist_assets ADD COLUMN analysis_snapshot JSONB;
            END IF;

            -- Widen futures_direction to VARCHAR(10) only if currently narrower.
            -- character_maximum_length is NULL for unlimited types; treat that as already wide enough.
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'pipeline_watchlist_assets'
                  AND column_name = 'futures_direction'
                  AND character_maximum_length IS NOT NULL
                  AND character_maximum_length < 10
            ) THEN
                ALTER TABLE pipeline_watchlist_assets ALTER COLUMN futures_direction TYPE VARCHAR(10);
            END IF;

            -- ── pipeline_watchlist_rejections ────────────────────────────
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_rejections' AND column_name = 'execution_id') THEN
                ALTER TABLE pipeline_watchlist_rejections ADD COLUMN execution_id UUID;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'pipeline_watchlist_rejections' AND column_name = 'analysis_snapshot') THEN
                ALTER TABLE pipeline_watchlist_rejections ADD COLUMN analysis_snapshot JSONB;
            END IF;

            -- ── watchlist_profiles ───────────────────────────────────────
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'watchlist_profiles' AND column_name = 'profile_type') THEN
                ALTER TABLE watchlist_profiles ADD COLUMN profile_type VARCHAR(10) DEFAULT 'L2';
            END IF;

            -- Drop legacy unique constraint that was replaced by (user_id, watchlist_id, profile_type)
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'watchlist_profiles'
                  AND constraint_name = 'watchlist_profiles_user_id_watchlist_id_key'
            ) THEN
                ALTER TABLE watchlist_profiles DROP CONSTRAINT watchlist_profiles_user_id_watchlist_id_key;
            END IF;

            -- ── market_metadata (best-effort columns in init_db) ─────────
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'market_metadata' AND column_name = 'spread_pct') THEN
                ALTER TABLE market_metadata ADD COLUMN spread_pct DECIMAL(10,4);
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'market_metadata' AND column_name = 'orderbook_depth_usdt') THEN
                ALTER TABLE market_metadata ADD COLUMN orderbook_depth_usdt DECIMAL(20,2);
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'market_metadata' AND column_name = 'volume_24h_updated_at') THEN
                ALTER TABLE market_metadata ADD COLUMN volume_24h_updated_at TIMESTAMPTZ;
            END IF;

            -- ── ohlcv (raw-SQL table) ────────────────────────────────────
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'ohlcv' AND column_name = 'quote_volume') THEN
                ALTER TABLE ohlcv ADD COLUMN quote_volume DECIMAL(20,4);
            END IF;

            -- ── trades (Trade ORM — CRITICAL) ────────────────────────────
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'trades' AND column_name = 'exchange_order_id') THEN
                ALTER TABLE trades ADD COLUMN exchange_order_id VARCHAR(100);
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'trades' AND column_name = 'source') THEN
                ALTER TABLE trades ADD COLUMN source VARCHAR(30) DEFAULT 'scalpyn';
            END IF;
        END $$;
    """))

    # ── Indexes (CREATE INDEX IF NOT EXISTS is idempotent on its own) ────────
    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_trades_exchange_order_id
        ON trades (exchange_order_id)
        WHERE exchange_order_id IS NOT NULL;
    """))


def downgrade() -> None:
    # Intentionally empty: dropping these columns would break the live ORM
    # (every model select() expands to all columns).  Roll back via a fresh
    # forward migration if needed.
    pass
