import asyncio
import logging
from typing import Union
from .database import engine, Base
from .models import *  # This ensures all models are registered
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def backfill_execution_tracking_columns(db: Union[AsyncSession, AsyncConnection]) -> None:
    """Add missing pipeline execution_id columns; caller owns transaction commit."""
    await db.execute(text("""
        ALTER TABLE pipeline_watchlist_assets
        ADD COLUMN IF NOT EXISTS execution_id UUID;
    """))
    await db.execute(text("""
        ALTER TABLE pipeline_watchlist_rejections
        ADD COLUMN IF NOT EXISTS execution_id UUID;
    """))


async def init_db():
    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Add missing columns to existing tables (migrations)
        try:
            await conn.execute(text("""
                ALTER TABLE pools ADD COLUMN IF NOT EXISTS overrides JSONB DEFAULT '{}';
            """))
            logger.info("Added 'overrides' column to pools table (or already exists)")
        except Exception as e:
            logger.warning(f"Could not add 'overrides' column: {e}")

        try:
            await conn.execute(text("""
                ALTER TABLE pools ADD COLUMN IF NOT EXISTS autopilot_enabled BOOLEAN NOT NULL DEFAULT false;
            """))
            logger.info("Added 'autopilot_enabled' column to pools table (or already exists)")
        except Exception as e:
            logger.warning(f"Could not add 'autopilot_enabled' column: {e}")

        try:
            await backfill_execution_tracking_columns(conn)
            logger.info("Ensured pipeline execution tracking columns exist")
        except Exception as e:
            logger.warning(f"Could not add pipeline execution tracking columns: {e}")

        # Ensure pipeline staleness-tracking and analysis columns exist on existing tables.
        # These are added by migrations 013/018 but may be absent if those migrations
        # failed (e.g., blocked by a DuplicateColumnError in an earlier migration).
        try:
            await conn.execute(text("""
                ALTER TABLE pipeline_watchlists
                    ADD COLUMN IF NOT EXISTS last_scanned_at TIMESTAMPTZ;
            """))
            await conn.execute(text("""
                ALTER TABLE pipeline_watchlist_assets
                    ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMPTZ;
            """))
            await conn.execute(text("""
                ALTER TABLE pipeline_watchlist_assets
                    ADD COLUMN IF NOT EXISTS analysis_snapshot JSONB;
            """))
            await conn.execute(text("""
                ALTER TABLE pipeline_watchlist_rejections
                    ADD COLUMN IF NOT EXISTS analysis_snapshot JSONB;
            """))
            logger.info("Ensured pipeline staleness and analysis_snapshot columns exist")
        except Exception as e:
            logger.warning(f"Could not add pipeline staleness/analysis columns: {e}")
        
        # Ensure profiles and watchlist_profiles tables exist with all columns
        try:
            await conn.execute(text("""
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
            await conn.execute(text("""
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
            # Add profile_type column if missing (migration)
            await conn.execute(text("""
                ALTER TABLE watchlist_profiles 
                ADD COLUMN IF NOT EXISTS profile_type VARCHAR(10) DEFAULT 'L2';
            """))
            # Drop old unique constraint and add new one
            try:
                await conn.execute(text("""
                    ALTER TABLE watchlist_profiles DROP CONSTRAINT IF EXISTS watchlist_profiles_user_id_watchlist_id_key;
                """))
            except Exception:
                pass
            logger.info("Profiles tables created or already exist")
        except Exception as e:
            logger.warning(f"Could not create profiles tables: {e}")
        
        # Create TimescaleDB hypertables if they don't exist
        # OHLCV
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ohlcv (
              time TIMESTAMPTZ NOT NULL,
              symbol VARCHAR(20) NOT NULL,
              exchange VARCHAR(50) NOT NULL,
              timeframe VARCHAR(10) NOT NULL,
              open DECIMAL(20,8),
              high DECIMAL(20,8),
              low DECIMAL(20,8),
              close DECIMAL(20,8),
              volume DECIMAL(20,4),
              quote_volume DECIMAL(20,4)
            );
        """))
        try:
            await conn.execute(text("""
                ALTER TABLE ohlcv
                  ADD COLUMN IF NOT EXISTS quote_volume DECIMAL(20,4);
            """))
            await conn.execute(text("""
                WITH ranked AS (
                    SELECT
                        ctid,
                        ROW_NUMBER() OVER (
                            PARTITION BY symbol, exchange, timeframe, time
                            ORDER BY ctid DESC
                        ) AS row_num
                    FROM ohlcv
                )
                DELETE FROM ohlcv
                WHERE ctid IN (
                    SELECT ctid
                    FROM ranked
                    WHERE row_num > 1
                );
            """))
            await conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS ix_ohlcv_symbol_exchange_timeframe_time
                ON ohlcv (symbol, exchange, timeframe, time);
            """))
        except Exception as e:
            logger.warning(f"Could not add quote_volume column, deduplicate rows, or create unique index on ohlcv: {e}")
        # Indicators
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS indicators (
              time TIMESTAMPTZ NOT NULL,
              symbol VARCHAR(20) NOT NULL,
              timeframe VARCHAR(10) NOT NULL,
              indicators_json JSONB NOT NULL
            );
        """))
        # Alpha Scores
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alpha_scores (
              time TIMESTAMPTZ NOT NULL,
              symbol VARCHAR(20) NOT NULL,
              score DECIMAL(5,2) NOT NULL,
              liquidity_score DECIMAL(5,2),
              market_structure_score DECIMAL(5,2),
              momentum_score DECIMAL(5,2),
              signal_score DECIMAL(5,2),
              components_json JSONB
            );
        """))
        # Funding Rates
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS funding_rates (
              time TIMESTAMPTZ NOT NULL,
              symbol VARCHAR(20) NOT NULL,
              exchange VARCHAR(50) NOT NULL,
              rate DECIMAL(10,6)
            );
        """))
        # Market Metadata (key-value approach, not hypertable)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS market_metadata (
              symbol VARCHAR(20) PRIMARY KEY,
              name VARCHAR(255),
              market_cap DECIMAL(20,2),
              volume_24h DECIMAL(20,2),
              price DECIMAL(20,8),
              price_change_24h DECIMAL(10,4),
              ranking INTEGER,
              spread_pct DECIMAL(10,4),
              orderbook_depth_usdt DECIMAL(20,2),
              last_updated TIMESTAMPTZ
            );
        """))
        # Add liquidity columns to market_metadata if they don't exist (migration).
        # Note on freshness columns:
        #   - `last_updated` is generic metadata freshness — touched by ANY
        #     write (price seed, orderbook, indicators, ticker).
        #   - `volume_24h_updated_at` is volume-specific freshness — written
        #     ONLY by ticker-based writers in `collect_market_data`. Use this
        #     column when checking whether `volume_24h` itself is stale.
        try:
            await conn.execute(text("""
                ALTER TABLE market_metadata
                  ADD COLUMN IF NOT EXISTS spread_pct DECIMAL(10,4),
                  ADD COLUMN IF NOT EXISTS orderbook_depth_usdt DECIMAL(20,2),
                  ADD COLUMN IF NOT EXISTS volume_24h_updated_at TIMESTAMPTZ;
            """))
        except Exception as e:
            logger.warning(f"Could not add liquidity columns to market_metadata: {e}")

        try:
            await conn.execute(text("""
                ALTER TABLE trades
                  ADD COLUMN IF NOT EXISTS exchange_order_id VARCHAR(100),
                  ADD COLUMN IF NOT EXISTS source VARCHAR(30) DEFAULT 'scalpyn';
            """))
            await conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS ix_trades_exchange_order_id
                ON trades (exchange_order_id)
                WHERE exchange_order_id IS NOT NULL;
            """))
            logger.info("Ensured trades.exchange_order_id and trades.source columns exist")
        except Exception as e:
            logger.warning(f"Could not add exchange_order_id/source columns to trades: {e}")
    # Attempt to create hypertables in separate transactions so failures don't abort the connection
    tables_to_hyper = ['ohlcv', 'indicators', 'alpha_scores', 'funding_rates']
    for table in tables_to_hyper:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"SELECT create_hypertable('{table}', 'time', if_not_exists => TRUE);"))
        except Exception as e:
            logger.warning(f"TimescaleDB hypertable for {table} skipped or unavailable: {e}")

    logger.info("Database schema initialized successfully.")

if __name__ == "__main__":
    asyncio.run(init_db())
