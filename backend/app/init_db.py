import asyncio
import logging
from .database import engine, Base
from .models import *  # This ensures all models are registered
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def ensure_pipeline_execution_tracking_schema(db) -> None:
    await db.execute(text("""
        ALTER TABLE pipeline_watchlist_assets
        ADD COLUMN IF NOT EXISTS execution_id UUID;
    """))
    await db.execute(text("""
        ALTER TABLE pipeline_watchlist_rejections
        ADD COLUMN IF NOT EXISTS execution_id UUID;
    """))

    commit = getattr(db, "commit", None)
    if callable(commit):
        await commit()


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
            await ensure_pipeline_execution_tracking_schema(conn)
            logger.info("Ensured pipeline execution tracking columns exist")
        except Exception as e:
            logger.warning(f"Could not add pipeline execution tracking columns: {e}")
        
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
              volume DECIMAL(20,4)
            );
        """))
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
        # Add liquidity columns to market_metadata if they don't exist (migration)
        try:
            await conn.execute(text("""
                ALTER TABLE market_metadata
                  ADD COLUMN IF NOT EXISTS spread_pct DECIMAL(10,4),
                  ADD COLUMN IF NOT EXISTS orderbook_depth_usdt DECIMAL(20,2);
            """))
        except Exception as e:
            logger.warning(f"Could not add liquidity columns to market_metadata: {e}")
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
