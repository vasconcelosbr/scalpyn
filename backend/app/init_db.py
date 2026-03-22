import asyncio
import logging
from .database import engine, Base
from .models import *  # This ensures all models are registered
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        
        # Add market_type column to pools
        try:
            await conn.execute(text("""
                ALTER TABLE pools ADD COLUMN IF NOT EXISTS market_type VARCHAR(20) DEFAULT 'spot';
            """))
            logger.info("Added 'market_type' column to pools table (or already exists)")
        except Exception as e:
            logger.warning(f"Could not add 'market_type' column: {e}")
        
        # Add profile_id column to pools
        try:
            await conn.execute(text("""
                ALTER TABLE pools ADD COLUMN IF NOT EXISTS profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL;
            """))
            logger.info("Added 'profile_id' column to pools table (or already exists)")
        except Exception as e:
            logger.warning(f"Could not add 'profile_id' column: {e}")

        # Add description column to pools (if missing from early schema)
        try:
            await conn.execute(text(
                "ALTER TABLE pools ADD COLUMN IF NOT EXISTS description TEXT;"
            ))
        except Exception as e:
            logger.warning(f"Could not add 'description' column to pools: {e}")

        # Add updated_at column to pools (if missing from early schema)
        try:
            await conn.execute(text(
                "ALTER TABLE pools ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();"
            ))
        except Exception as e:
            logger.warning(f"Could not add 'updated_at' column to pools: {e}")

        # Add discovery fields to pool_coins
        try:
            await conn.execute(text(
                "ALTER TABLE pool_coins ADD COLUMN IF NOT EXISTS origin VARCHAR(20) DEFAULT 'manual';"
            ))
            await conn.execute(text(
                "ALTER TABLE pool_coins ADD COLUMN IF NOT EXISTS discovered_at TIMESTAMPTZ;"
            ))
            logger.info("Added discovery fields to pool_coins (or already exist)")
        except Exception as e:
            logger.warning(f"Could not add discovery fields to pool_coins: {e}")
        
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
              last_updated TIMESTAMPTZ
            );
        """))
    # Attempt to create hypertables in separate transactions so failures don't abort the connection
    tables_to_hyper = ['ohlcv', 'indicators', 'alpha_scores', 'funding_rates']
    for table in tables_to_hyper:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"SELECT create_hypertable('{table}', 'time', if_not_exists => TRUE);"))
        except Exception as e:
            logger.warning(f"TimescaleDB hypertable for {table} skipped or unavailable: {e}")

    # ── Pipeline Watchlist tables (additive — never drops existing tables) ───────
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS pipeline_watchlists (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name VARCHAR(100) NOT NULL,
                    level VARCHAR(10) NOT NULL DEFAULT 'custom',
                    source_pool_id UUID REFERENCES pools(id) ON DELETE SET NULL,
                    source_watchlist_id UUID REFERENCES pipeline_watchlists(id) ON DELETE SET NULL,
                    profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
                    auto_refresh BOOLEAN DEFAULT TRUE,
                    filters_json JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS pipeline_watchlist_assets (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    watchlist_id UUID NOT NULL
                        REFERENCES pipeline_watchlists(id) ON DELETE CASCADE,
                    symbol VARCHAR(20) NOT NULL,
                    current_price DECIMAL(20,8),
                    price_change_24h DECIMAL(8,4),
                    volume_24h DECIMAL(20,2),
                    market_cap DECIMAL(20,2),
                    alpha_score DECIMAL(5,2),
                    entered_at TIMESTAMPTZ DEFAULT NOW(),
                    previous_level VARCHAR(10),
                    level_change_at TIMESTAMPTZ,
                    level_direction VARCHAR(4),
                    UNIQUE(watchlist_id, symbol)
                );
            """))
        logger.info("Pipeline watchlist tables created or already exist.")
    except Exception as e:
        logger.warning(f"Could not create pipeline watchlist tables: {e}")

    logger.info("Database schema initialized successfully.")

if __name__ == "__main__":
    asyncio.run(init_db())
