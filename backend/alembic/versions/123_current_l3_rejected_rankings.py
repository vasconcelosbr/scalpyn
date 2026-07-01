"""Attach current L3 rejected shadows to their profile and rank them.

Revision ID: 123_current_l3_rejected_rankings
Revises: 122_backfill_ranking_dec_id
Create Date: 2026-07-01
"""

from alembic import op
from sqlalchemy import text


revision = "123_current_l3_rejected_rankings"
down_revision = "122_backfill_ranking_dec_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only rows created by a watchlist that still exists are eligible.  The
    # timestamp guard excludes any historical row that merely happens to carry
    # a reused identifier and keeps this migration scoped to the new lineage.
    op.execute(text("""
        WITH candidates AS (
            SELECT st.id,
                   pw.profile_id,
                   p.name AS profile_name,
                   p.updated_at AS profile_version,
                   ROW_NUMBER() OVER (
                       PARTITION BY pw.profile_id, st.symbol, st.source,
                                    shadow_lab_hour_bucket(st.created_at)
                       ORDER BY st.created_at, st.id
                   ) AS bucket_rank
            FROM shadow_trades AS st
            JOIN pipeline_watchlists AS pw
              ON pw.user_id = st.user_id
             AND pw.id = st.watchlist_id
             AND UPPER(pw.level) = 'L3'
            JOIN profiles AS p
              ON p.id = pw.profile_id
             AND p.user_id = pw.user_id
            WHERE st.source = 'L3_REJECTED'
              AND st.profile_id IS NULL
              AND st.created_at >= pw.created_at
        )
        UPDATE shadow_trades AS st
           SET profile_id = pw.profile_id,
               profile_name = pw.profile_name,
               profile_version = pw.profile_version,
               strategy_type = 'PROFILE_L3',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_123_current_watchlist_pair',
               lineage_resolved_at = now()
          FROM candidates AS pw
         WHERE st.id = pw.id
           AND pw.bucket_rank = 1
           AND NOT EXISTS (
               SELECT 1
               FROM shadow_trades AS existing
               WHERE existing.profile_id = pw.profile_id
                 AND existing.symbol = st.symbol
                 AND existing.source = st.source
                 AND shadow_lab_hour_bucket(existing.created_at)
                     = shadow_lab_hour_bucket(st.created_at)
           )
    """))

    # The ranking contract is DB-backed.  Append the new source without
    # replacing user-defined weights, limits, penalties, or thresholds.
    op.execute(text("""
        UPDATE config_profiles
           SET config_json = jsonb_set(
                   config_json,
                   '{source_filter}',
                   COALESCE(config_json->'source_filter', '[]'::jsonb)
                     || '["L3_REJECTED"]'::jsonb,
                   true
               ),
               updated_at = now()
         WHERE config_type = 'watchlist_performance_ranking'
           AND is_active = true
           AND pool_id IS NULL
           AND NOT COALESCE(config_json->'source_filter', '[]'::jsonb)
                   @> '["L3_REJECTED"]'::jsonb
    """))


def downgrade() -> None:
    op.execute(text("""
        UPDATE config_profiles
           SET config_json = jsonb_set(
                   config_json,
                   '{source_filter}',
                   COALESCE(
                       (
                           SELECT jsonb_agg(value)
                           FROM jsonb_array_elements(
                               COALESCE(config_json->'source_filter', '[]'::jsonb)
                           ) AS item(value)
                           WHERE value <> '"L3_REJECTED"'::jsonb
                       ),
                       '[]'::jsonb
                   ),
                   true
               ),
               updated_at = now()
         WHERE config_type = 'watchlist_performance_ranking'
           AND is_active = true
           AND pool_id IS NULL
    """))

    op.execute(text("""
        UPDATE shadow_trades
           SET profile_id = NULL,
               profile_name = NULL,
               profile_version = NULL,
               strategy_type = NULL,
               lineage_confidence = 'EXACT',
               lineage_source = 'pipeline_scan',
               lineage_resolved_at = now()
         WHERE lineage_source = 'migration_123_current_watchlist_pair'
    """))
