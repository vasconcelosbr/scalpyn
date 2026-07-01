"""Repair current L3 rejected shadow ranking backfill.

Revision ID: 124_repair_current_l3_rankings
Revises: 123_current_l3_rejected_rankings
Create Date: 2026-07-01

Revision 123 was stamped in production after its data update collided with the
active-shadow uniqueness constraint.  This repair repeats the intended update
idempotently while satisfying both shadow-trade uniqueness contracts.
"""

from alembic import op
from sqlalchemy import text


revision = "124_repair_current_l3_rankings"
down_revision = "123_current_l3_rejected_rankings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        WITH raw_candidates AS (
            SELECT st.id,
                   st.status,
                   st.created_at,
                   st.symbol,
                   st.source,
                   pw.profile_id,
                   p.name AS profile_name,
                   p.updated_at AS profile_version
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
        ),
        active_candidates AS (
            SELECT raw.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY raw.profile_id, raw.symbol, raw.source
                       ORDER BY raw.created_at, raw.id
                   ) AS active_rank
            FROM raw_candidates AS raw
            WHERE raw.status IN ('RUNNING', 'PENDING')
        ),
        active_deduplicated AS (
            SELECT raw.*
            FROM raw_candidates AS raw
            LEFT JOIN active_candidates AS active ON active.id = raw.id
            WHERE raw.status NOT IN ('RUNNING', 'PENDING')
               OR active.active_rank = 1
        ),
        candidates AS (
            SELECT deduped.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY deduped.profile_id,
                                    deduped.symbol,
                                    deduped.source,
                                    shadow_lab_hour_bucket(deduped.created_at)
                       ORDER BY deduped.created_at, deduped.id
                   ) AS bucket_rank
            FROM active_deduplicated AS deduped
        )
        UPDATE shadow_trades AS st
           SET profile_id = candidate.profile_id,
               profile_name = candidate.profile_name,
               profile_version = candidate.profile_version,
               strategy_type = 'PROFILE_L3',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_124_current_watchlist_pair',
               lineage_resolved_at = now()
          FROM candidates AS candidate
         WHERE st.id = candidate.id
           AND candidate.bucket_rank = 1
           AND NOT EXISTS (
               SELECT 1
               FROM shadow_trades AS existing
               WHERE existing.profile_id = candidate.profile_id
                 AND existing.symbol = candidate.symbol
                 AND existing.source = candidate.source
                 AND shadow_lab_hour_bucket(existing.created_at)
                     = shadow_lab_hour_bucket(candidate.created_at)
           )
           AND (
               candidate.status NOT IN ('RUNNING', 'PENDING')
               OR NOT EXISTS (
                   SELECT 1
                   FROM shadow_trades AS existing_active
                   WHERE existing_active.profile_id = candidate.profile_id
                     AND existing_active.symbol = candidate.symbol
                     AND existing_active.source = candidate.source
                     AND existing_active.status IN ('RUNNING', 'PENDING')
               )
           )
    """))

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
         WHERE lineage_source = 'migration_124_current_watchlist_pair'
    """))
