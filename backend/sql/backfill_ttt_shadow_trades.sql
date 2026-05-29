-- Backfill TTT fields on completed shadow_trades that predate the TTT implementation
-- (migration 065_ttt_shadow_columns, 2026-05-28).
--
-- Why this script exists:
--   shadow_trades created before 065 have ttt_enabled=FALSE (default). The
--   ttt_analyzer only processes rows where ttt_enabled=TRUE, so historical
--   trades never get FAST_WIN/TIMEOUT labels. Without backfill the ML trainer
--   has zero TTT-labeled samples on its first post-065 run.
--
-- What this does:
--   1. Sets ttt_enabled=TRUE on all COMPLETED shadows with pnl_pct resolved
--      so the ttt_analyzer can compute ttt_outcome retroactively via OHLCV.
--   2. Seeds ttt_tp_pct and ttt_timeout_minutes with the canonical defaults
--      from the ttt_policy config (1.0% / 180 min).
--   3. Sets ttt_analysis_done=FALSE so the analyzer picks them up on next run.
--
-- Idempotent: WHERE guards against re-processing already-enabled rows.
-- Safe to run multiple times.
--
-- Apply via Cloud SQL import (same pattern as seed_ttt_policy.sql):
--   gsutil cp backend/sql/backfill_ttt_shadow_trades.sql gs://scalpyn-mlflow/sql/
--   gcloud sql import sql scalpyndata gs://scalpyn-mlflow/sql/backfill_ttt_shadow_trades.sql \
--     --database=scalpyn --user=postgres
--   gsutil rm gs://scalpyn-mlflow/sql/backfill_ttt_shadow_trades.sql

BEGIN;

UPDATE shadow_trades
SET
    ttt_enabled          = TRUE,
    ttt_tp_pct           = 1.0,
    ttt_timeout_minutes  = 180,
    ttt_analysis_done    = FALSE
WHERE
    status               = 'COMPLETED'
    AND pnl_pct          IS NOT NULL
    AND ttt_enabled      = FALSE;

-- Report how many rows were updated
DO $$
DECLARE
    n_updated INTEGER;
BEGIN
    GET DIAGNOSTICS n_updated = ROW_COUNT;
    RAISE NOTICE 'backfill_ttt_shadow_trades: updated % rows', n_updated;
END;
$$;

COMMIT;
