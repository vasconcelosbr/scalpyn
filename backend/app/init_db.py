"""
Schema bootstrap for the Scalpyn backend.

Migration policy:
  - CRITICAL blocks (columns/tables referenced by SQLAlchemy ORM models)
    log at error-level WITH stack trace and re-raise. Failure aborts
    startup so Cloud Run never serves traffic with a schema mismatch
    (which would surface as opaque "Database error" 503s on every
    `select(Model)`).
  - BEST-EFFORT blocks (raw-SQL tables, env-specific extensions like
    TimescaleDB hypertables, optional analytics columns) log at
    warning-level and continue. Each such block is annotated inline
    with WHY it is non-fatal.
"""
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
    # Robust Indicators Phase 2 — engine_tag mirrors Alembic migration 028.
    # Kept in init_db so fresh containers without the migration history still
    # carry the column needed by the rollout bucketing path.
    await db.execute(text("""
        ALTER TABLE pipeline_watchlist_assets
        ADD COLUMN IF NOT EXISTS engine_tag VARCHAR(16);
    """))
    await db.execute(text("""
        ALTER TABLE pipeline_watchlist_rejections
        ADD COLUMN IF NOT EXISTS engine_tag VARCHAR(16);
    """))


async def init_db():
    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Critical migrations below add columns/tables that the SQLAlchemy
        # ORM declares on its models. They MUST log errors with stack trace
        # and re-raise — silent failures cause `select(Model)` to crash with
        # opaque "Database error" 503s on every request. IF NOT EXISTS keeps
        # them idempotent on successful runs.

        try:
            await conn.execute(text("""
                ALTER TABLE pools ADD COLUMN IF NOT EXISTS overrides JSONB DEFAULT '{}';
            """))
            await conn.execute(text("""
                ALTER TABLE pools ADD COLUMN IF NOT EXISTS autopilot_enabled BOOLEAN NOT NULL DEFAULT false;
            """))
            logger.info("Ensured pools.overrides and pools.autopilot_enabled columns exist")
        except Exception as e:
            logger.error("Failed to migrate pools columns (Pool ORM): %s", e, exc_info=True)
            raise

        try:
            await backfill_execution_tracking_columns(conn)
            logger.info("Ensured pipeline execution tracking columns exist")
        except Exception as e:
            logger.error("Failed to add pipeline execution_id columns: %s", e, exc_info=True)
            raise

        # CRITICAL: Futures Mode columns — referenced by PipelineWatchlist and
        # PipelineWatchlistAsset ORM models. Failing here aborts startup so the
        # schema mismatch never silently breaks the pipeline scan task.
        try:
            await conn.execute(text("""
                ALTER TABLE pipeline_watchlists
                    ADD COLUMN IF NOT EXISTS market_mode VARCHAR(10) NOT NULL DEFAULT 'spot';
            """))
            await conn.execute(text("""
                ALTER TABLE pipeline_watchlist_assets
                    ADD COLUMN IF NOT EXISTS score_long NUMERIC(5,2),
                    ADD COLUMN IF NOT EXISTS score_short NUMERIC(5,2),
                    ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(5,2),
                    ADD COLUMN IF NOT EXISTS futures_direction VARCHAR(10),
                    ADD COLUMN IF NOT EXISTS entry_long_blocked BOOLEAN NOT NULL DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS entry_short_blocked BOOLEAN NOT NULL DEFAULT FALSE;
            """))
            logger.info("Ensured futures mode columns exist on pipeline_watchlists and pipeline_watchlist_assets")
        except Exception as e:
            logger.error("Failed to add futures mode columns: %s", e, exc_info=True)
            raise

        try:
            await conn.execute(text("""
                ALTER TABLE pipeline_watchlist_assets
                    ALTER COLUMN futures_direction TYPE VARCHAR(10);
            """))
            logger.info("Ensured futures_direction column is VARCHAR(10)")
        except Exception as e:
            logger.error("Failed to widen futures_direction column: %s", e, exc_info=True)
            raise

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
            logger.error("Failed to add pipeline staleness/analysis columns: %s", e, exc_info=True)
            raise

        # profiles + watchlist_profiles: required by the AI Skills feature.
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
            # Drop old unique constraint and add new one (best-effort —
            # constraint may not exist yet on fresh DBs).
            try:
                await conn.execute(text("""
                    ALTER TABLE watchlist_profiles DROP CONSTRAINT IF EXISTS watchlist_profiles_user_id_watchlist_id_key;
                """))
            except Exception:
                pass
            logger.info("Profiles tables created or already exist")
        except Exception as e:
            logger.error(
                "FATAL: failed to create/migrate profiles + watchlist_profiles "
                "tables (required by AI Skills feature). "
                "Aborting startup. Error: %s",
                e,
                exc_info=True,
            )
            raise
        
        # Create TimescaleDB hypertables if they don't exist
        # OHLCV
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ohlcv (
              time TIMESTAMPTZ NOT NULL,
              symbol VARCHAR(20) NOT NULL,
              exchange VARCHAR(50) NOT NULL,
              timeframe VARCHAR(10) NOT NULL,
              market_type VARCHAR(10) NOT NULL DEFAULT 'spot',
              open DECIMAL(20,8),
              high DECIMAL(20,8),
              low DECIMAL(20,8),
              close DECIMAL(20,8),
              volume DECIMAL(20,4),
              quote_volume DECIMAL(20,4)
            );
        """))
        # Best-effort: ohlcv has no SQLAlchemy ORM model — it's accessed via
        # raw SQL only. Failing here doesn't break ORM queries; warn and
        # continue so the rest of startup completes.
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
              market_type VARCHAR(10) NOT NULL DEFAULT 'spot',
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
        # Best-effort: market_metadata is accessed via raw SQL (no ORM model
        # references these columns), so a missing column degrades the
        # liquidity panel but does not crash core endpoints.
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

        # CRITICAL migration: the SQLAlchemy Trade model declares these columns,
        # so every `select(Trade)` in the app expands to all columns. If this
        # ALTER TABLE silently fails on production, the dashboard returns a
        # generic "Database error" 503 on every request. Fail loud and abort
        # startup so the broken schema never serves traffic.
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
            logger.error(
                "FATAL: failed to add exchange_order_id/source columns to trades. "
                "The Trade ORM model requires these columns; running with the old "
                "schema will break every /analytics endpoint. Aborting startup. "
                "Error: %s",
                e,
                exc_info=True,
            )
            raise
        # BEST-EFFORT: add scheduler_group to indicators table for dual-scheduler
        # architecture (Task #95).  Non-fatal because the column is not referenced
        # by any ORM model — it's read/written via raw SQL only.
        try:
            await conn.execute(text("""
                ALTER TABLE indicators
                    ADD COLUMN IF NOT EXISTS scheduler_group VARCHAR(20) DEFAULT 'combined';
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_indicators_symbol_group_time
                ON indicators (symbol, scheduler_group, time DESC);
            """))
            logger.info("Ensured indicators.scheduler_group column and index exist")
        except Exception as e:
            logger.warning(
                "Could not add scheduler_group to indicators (non-fatal, "
                "dual-scheduler will fall back gracefully): %s", e
            )
        # BEST-EFFORT: add market_type to indicators table for spot/futures
        # pool segregation.  Non-fatal — existing rows default to 'spot'.
        try:
            await conn.execute(text("""
                ALTER TABLE indicators
                    ADD COLUMN IF NOT EXISTS market_type VARCHAR(10) NOT NULL DEFAULT 'spot';
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_indicators_futures_time
                ON indicators (time DESC)
                WHERE market_type = 'futures';
            """))
            logger.info("Ensured indicators.market_type column and index exist")
        except Exception as e:
            logger.warning(
                "Could not add market_type to indicators (non-fatal — "
                "all rows will default to 'spot'): %s", e
            )
        # BEST-EFFORT: add a unique constraint on (time, symbol, timeframe) so that
        # ON CONFLICT DO UPDATE in _persist_indicators has a valid conflict target and
        # prevents duplicate indicator rows from accumulating.  Non-fatal — if the
        # table already contains duplicate rows the index creation will fail; in that
        # case the table should be deduplicated manually before this succeeds.
        try:
            await conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_indicators_time_symbol_timeframe
                ON indicators (time, symbol, timeframe);
            """))
            logger.info("Ensured unique index on indicators(time, symbol, timeframe)")
        except Exception as e:
            logger.warning(
                "Could not add unique index to indicators (non-fatal — "
                "table may contain duplicate rows; ON CONFLICT will use DO NOTHING "
                "as fallback): %s", e
            )

    # Attempt to create hypertables in separate transactions so failures don't abort the connection
    # ── Robust indicators (Phase 1): indicator_snapshots ─────────────────
    # Mirrors alembic revision 027. Best-effort so dev DBs that have not yet
    # run `alembic upgrade head` still get the table.
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS indicator_snapshots (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    symbol VARCHAR(40) NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
                    indicators_json JSONB NOT NULL,
                    global_confidence NUMERIC(6,4),
                    valid_indicators INTEGER,
                    total_indicators INTEGER,
                    validation_passed BOOLEAN,
                    validation_errors JSONB,
                    score NUMERIC(7,4),
                    score_confidence NUMERIC(6,4),
                    can_trade BOOLEAN,
                    legacy_score NUMERIC(7,4),
                    divergence_bucket VARCHAR(16),
                    rejection_reason VARCHAR(255),
                    user_id UUID,
                    watchlist_id UUID
                );
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_indicator_snapshots_symbol_time
                    ON indicator_snapshots (symbol, timestamp DESC);
            """))
    except Exception as e:
        logger.warning("Could not create indicator_snapshots table (non-fatal): %s", e)

    tables_to_hyper = ['ohlcv', 'indicators', 'alpha_scores', 'funding_rates', 'indicator_snapshots']
    for table in tables_to_hyper:
        # indicator_snapshots uses 'timestamp' as the time column; the rest use 'time'.
        time_col = 'timestamp' if table == 'indicator_snapshots' else 'time'
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"SELECT create_hypertable('{table}', '{time_col}', if_not_exists => TRUE);"))
        except Exception as e:
            logger.warning(f"TimescaleDB hypertable for {table} skipped or unavailable: {e}")

    logger.info("Database schema initialized successfully.")

if __name__ == "__main__":
    asyncio.run(init_db())
