-- ============================================================================
-- SQL MIGRATION SCRIPTS FOR MANUAL EXECUTION
-- ============================================================================
--
-- IMPORTANT: DO NOT EXECUTE AUTOMATICALLY
-- These scripts must be reviewed and executed manually by a DBA
--
-- Purpose: Add constraints and indexes to improve pipeline reliability
-- Tables: decisions_log
--
-- ============================================================================

-- ============================================================================
-- MIGRATION 1: Add UNIQUE constraint for decision deduplication
-- ============================================================================
--
-- Purpose: Prevent duplicate decision logs for the same symbol/strategy/direction
-- within a short time window. This enforces app-level deduplication at DB level.
--
-- Note: This constraint uses a partial index to only apply to recent decisions
-- (last 5 minutes) to avoid conflicts with historical data.
--
-- Execution notes:
-- 1. Verify no duplicate decisions exist in the last 5 minutes before applying
-- 2. This is a best-effort constraint - app-level deduplication is primary
-- 3. Index automatically created with UNIQUE constraint

-- First, check for existing duplicates (diagnostic query)
-- Run this BEFORE applying the constraint:

SELECT
    symbol,
    strategy,
    COALESCE(direction, 'NULL') as direction,
    COUNT(*) as duplicate_count,
    MAX(created_at) as latest_time
FROM decisions_log
WHERE created_at >= NOW() - INTERVAL '5 minutes'
GROUP BY symbol, strategy, COALESCE(direction, 'NULL')
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC
LIMIT 20;

-- If duplicates found, clean them up first (keep only the latest):
-- UNCOMMENT AND MODIFY AS NEEDED:
/*
DELETE FROM decisions_log
WHERE id IN (
    SELECT id
    FROM (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY symbol, strategy, COALESCE(direction, '')
                ORDER BY created_at DESC
            ) as rn
        FROM decisions_log
        WHERE created_at >= NOW() - INTERVAL '5 minutes'
    ) sub
    WHERE rn > 1
);
*/

-- Now apply the partial unique index:
-- This prevents duplicate decisions for the same symbol/strategy/direction
-- within the recent window (last 5 minutes)

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_decisions_log_dedup_recent
ON decisions_log (symbol, strategy, COALESCE(direction, ''))
WHERE created_at >= NOW() - INTERVAL '5 minutes';

-- Note: This is a partial index, so it won't enforce uniqueness on old data
-- The app-level deduplication (5-minute window check) handles the primary logic


-- ============================================================================
-- MIGRATION 2: Add composite index for deduplication queries
-- ============================================================================
--
-- Purpose: Optimize the deduplication query in _persist_decision_logs()
-- This speeds up the check for existing recent decisions

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_decisions_log_dedup_lookup
ON decisions_log (symbol, strategy, direction, created_at DESC)
WHERE created_at >= NOW() - INTERVAL '10 minutes';

-- This index covers the query:
-- SELECT DISTINCT symbol, strategy, direction
-- FROM decisions_log
-- WHERE created_at >= :recent_window
--   AND (symbol, strategy, COALESCE(direction, '')) IN :checks


-- ============================================================================
-- MIGRATION 3: Add index for simulation pipeline queries
-- ============================================================================
--
-- Purpose: Speed up queries that check which decisions need simulation
-- Used by simulation service to find decisions without simulations

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_decisions_log_for_simulation
ON decisions_log (created_at DESC, id)
WHERE decision = 'ALLOW';

-- This index optimizes:
-- SELECT * FROM decisions_log
-- WHERE decision = 'ALLOW'
-- ORDER BY created_at DESC
-- LIMIT 100


-- ============================================================================
-- MIGRATION 4: Optional - Add created_at default if missing
-- ============================================================================
--
-- Purpose: Ensure all new decision logs have a timestamp
-- This is defensive - the ORM already sets this

-- Check current default:
SELECT
    column_name,
    column_default,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'decisions_log'
  AND column_name = 'created_at';

-- If no default, add one:
-- ALTER TABLE decisions_log
-- ALTER COLUMN created_at SET DEFAULT NOW();


-- ============================================================================
-- MIGRATION 5: Add index for pipeline status endpoint
-- ============================================================================
--
-- Purpose: Optimize the /api/system/pipeline-status endpoint queries

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_decisions_log_status
ON decisions_log (created_at DESC, decision);

-- This index optimizes the status query:
-- SELECT COUNT(*) FILTER (WHERE decision = 'ALLOW') ...
-- FROM decisions_log


-- ============================================================================
-- VERIFICATION QUERIES
-- ============================================================================

-- Verify all indexes were created:
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'decisions_log'
ORDER BY indexname;

-- Check table statistics:
SELECT
    schemaname,
    tablename,
    n_live_tup as row_count,
    n_dead_tup as dead_rows,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE tablename = 'decisions_log';

-- Check index usage (run after a few hours of production use):
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan as index_scans,
    idx_tup_read as tuples_read,
    idx_tup_fetch as tuples_fetched
FROM pg_stat_user_indexes
WHERE tablename = 'decisions_log'
ORDER BY idx_scan DESC;


-- ============================================================================
-- ROLLBACK INSTRUCTIONS
-- ============================================================================
--
-- If you need to rollback these changes:

-- DROP INDEX CONCURRENTLY IF EXISTS idx_decisions_log_dedup_recent;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_decisions_log_dedup_lookup;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_decisions_log_for_simulation;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_decisions_log_status;


-- ============================================================================
-- END OF SQL MIGRATION SCRIPTS
-- ============================================================================
