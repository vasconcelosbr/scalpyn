-- Cleanup: duplicate active L3_LAB shadows (migration 092)
--
-- Root cause: uq_shadow_lab_profile_symbol_bucket only guards within the same
-- hour bucket. Shadows accumulated one per hour while the same symbol kept
-- passing the filter across hour boundaries.
--
-- Strategy: per (profile_id, symbol, source) keep the OLDEST active shadow
-- (earliest created_at) so the monitor can close it normally. Cancel the rest.
--
-- Run STEP 1 first to audit, then STEP 2 to apply.
-- Safe to re-run: WHERE rn > 1 only targets actual duplicates.

-- ── STEP 1: audit (read-only) ────────────────────────────────────────────────

SELECT
    profile_name,
    symbol,
    source,
    COUNT(*)            AS duplicate_count,
    MIN(created_at)     AS oldest,
    MAX(created_at)     AS newest,
    STRING_AGG(status, ', ' ORDER BY created_at) AS statuses
FROM shadow_trades
WHERE profile_id IS NOT NULL
  AND status IN ('RUNNING', 'PENDING')
GROUP BY profile_name, symbol, source
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, profile_name, symbol;


-- ── STEP 2: cleanup (write) ──────────────────────────────────────────────────

WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY profile_id, symbol, source
            ORDER BY created_at ASC   -- keep oldest
        ) AS rn
    FROM shadow_trades
    WHERE profile_id IS NOT NULL
      AND status IN ('RUNNING', 'PENDING')
)
UPDATE shadow_trades
SET
    status      = 'CANCELLED',
    skip_reason = 'dedup_cleanup_092'
WHERE id IN (
    SELECT id FROM ranked WHERE rn > 1
)
RETURNING id, symbol, profile_name, created_at;
