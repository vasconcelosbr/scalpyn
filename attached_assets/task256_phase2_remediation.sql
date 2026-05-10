-- Task #256 — Phase 2 remediation (operator-applied, NOT auto-migration)
--
-- Two corrections deferred from the original Phase 2 spec because both
-- carry data risk that requires explicit operator review:
--
--   (a) is_tradable=is_active 1:1 ratio  → reset is_tradable=false for
--       symbols never explicitly approved by an operator (legacy backfill
--       from migration 043 setting them all to true).
--
--   (b) decisions_log.outcome empty      → diagnostic, no data fix needed.
--       Column is populated by trade_monitor_service.py:386 when a trade
--       closes; with zero closed trades in the window, NULL is correct.
--
-- Run order: dry-run first (SELECTs), review, then execute the UPDATE.
-- All UPDATEs are wrapped in BEGIN/ROLLBACK so the operator must change
-- to COMMIT explicitly.

-- ─────────────────────────────────────────────────────────────────────────
-- (a.1) DRY-RUN — count what would be reset
-- ─────────────────────────────────────────────────────────────────────────
SELECT
  origin,
  COUNT(*)                                              AS total,
  COUNT(*) FILTER (WHERE is_tradable)                   AS currently_tradable,
  COUNT(*) FILTER (WHERE is_tradable AND NOT is_approved) AS would_reset
FROM pool_coins
WHERE is_active
GROUP BY origin
ORDER BY origin;

-- ─────────────────────────────────────────────────────────────────────────
-- (a.2) DRY-RUN — list specific symbols to be reset
-- ─────────────────────────────────────────────────────────────────────────
SELECT symbol, origin, is_active, is_approved, is_tradable, discovered_at
FROM pool_coins
WHERE is_active
  AND is_tradable
  AND NOT is_approved
ORDER BY symbol
LIMIT 200;

-- ─────────────────────────────────────────────────────────────────────────
-- (a.3) APPLY — reset is_tradable=false where the operator never explicitly
--       approved the symbol (is_approved=false). Preserves explicitly
--       operator-approved symbols. Run inside transaction; review then COMMIT.
-- ─────────────────────────────────────────────────────────────────────────
BEGIN;

UPDATE pool_coins
   SET is_tradable = false,
       updated_at  = NOW()
 WHERE is_active
   AND is_tradable
   AND NOT is_approved;

-- Sanity check after UPDATE
SELECT
  COUNT(*) FILTER (WHERE is_active)   AS active,
  COUNT(*) FILTER (WHERE is_tradable) AS tradable,
  COUNT(*) FILTER (WHERE is_active AND NOT is_tradable) AS active_not_tradable
FROM pool_coins;

-- ROLLBACK to discard, COMMIT to apply.
ROLLBACK;
-- COMMIT;

-- ─────────────────────────────────────────────────────────────────────────
-- (b.1) DIAGNOSTIC — decisions_log.outcome distribution last 7d
--      Expected: most NULL until trades close. ALLOW + linked trade
--      closing → outcome filled by trade_monitor_service.py:386.
-- ─────────────────────────────────────────────────────────────────────────
SELECT
  decision,
  outcome,
  COUNT(*) AS n
FROM decisions_log
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY 1, 2
ORDER BY 1, 2 NULLS LAST;

-- ─────────────────────────────────────────────────────────────────────────
-- (b.2) DIAGNOSTIC — verify the decision_id linkage actually fires
--      when trade_tracking rows close. Expect non-zero matched.
-- ─────────────────────────────────────────────────────────────────────────
SELECT
  COUNT(*)                                                   AS closed_trades_7d,
  COUNT(*) FILTER (WHERE decision_id IS NOT NULL)            AS with_decision_link,
  COUNT(*) FILTER (WHERE outcome IS NOT NULL)                AS with_trade_outcome
FROM trade_tracking
WHERE is_simulated = false
  AND status = 'closed'
  AND COALESCE(exit_time, entry_time) > NOW() - INTERVAL '7 days';
