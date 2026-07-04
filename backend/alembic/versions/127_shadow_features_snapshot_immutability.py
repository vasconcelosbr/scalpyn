"""Guard shadow trade feature snapshots.

Revision ID: 127_shadow_fs_immutable
Revises: 126_profile_intelligence_copilot
Create Date: 2026-07-03
"""

from alembic import op
from sqlalchemy import text


revision = "127_shadow_fs_immutable"
down_revision = "126_profile_intelligence_copilot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        UPDATE config_profiles
           SET config_json = config_json
               || jsonb_build_object(
                    'ml_backfill_marker_key',
                    COALESCE(config_json->>'ml_backfill_marker_key', '_directional_backfill'),
                    'ml_backfilled_feature_names',
                    COALESCE(
                        config_json->'ml_backfilled_feature_names',
                        '[
                          "adx_slope_3",
                          "rsi_slope_3",
                          "rsi_slope_5",
                          "macd_hist_slope_3",
                          "macd_hist_slope_5",
                          "higher_highs_5",
                          "higher_lows_5",
                          "vwap_reclaim_bool",
                          "ema21_ema50_distance_pct",
                          "di_plus_minus_diff"
                        ]'::jsonb
                    )
                  ),
               updated_at = now()
         WHERE config_type = 'ml'
    """))

    op.execute(text("""
        ALTER TABLE ml_dataset_readiness_reports
        ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb
    """))

    op.execute(text("""
        CREATE OR REPLACE FUNCTION prevent_shadow_features_snapshot_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF OLD.features_snapshot IS NOT NULL
             AND OLD.features_snapshot <> '{}'::jsonb
             AND NEW.features_snapshot IS DISTINCT FROM OLD.features_snapshot THEN
            RAISE EXCEPTION 'shadow_trades.features_snapshot is immutable after INSERT'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$;
    """))

    op.execute(text("""
        DROP TRIGGER IF EXISTS trg_shadow_features_snapshot_immutable ON shadow_trades
    """))
    op.execute(text("""
        CREATE TRIGGER trg_shadow_features_snapshot_immutable
        BEFORE UPDATE OF features_snapshot ON shadow_trades
        FOR EACH ROW
        EXECUTE FUNCTION prevent_shadow_features_snapshot_update()
    """))


def downgrade() -> None:
    op.execute(text("""
        DROP TRIGGER IF EXISTS trg_shadow_features_snapshot_immutable ON shadow_trades
    """))
    op.execute(text("""
        DROP FUNCTION IF EXISTS prevent_shadow_features_snapshot_update()
    """))
    op.execute(text("""
        ALTER TABLE ml_dataset_readiness_reports
        DROP COLUMN IF EXISTS metadata
    """))
