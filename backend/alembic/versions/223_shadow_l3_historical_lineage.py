"""Repair exact L3 lineage independently of the profile's current status.

Revision ID: 223_shadow_l3_historical_lineage
Revises: 222_shadow_l3_lineage_retry

An inactive profile cannot create a new opportunity, but its historical
Shadows remain reportable. This follow-up repairs only exact immutable
profile-version/config-hash matches with one associated L3 watchlist.
"""

from alembic import op
from sqlalchemy import text


revision = "223_shadow_l3_historical_lineage"
down_revision = "222_shadow_l3_lineage_retry"
branch_labels = None
depends_on = None

_MARKER = "migration_223_shadow_l3_historical_lineage"


def upgrade() -> None:
    op.execute(text(f"""
        WITH unique_l3 AS (
            SELECT st.id AS shadow_trade_id,
                   min(pw.id::text)::uuid AS watchlist_id,
                   count(*) AS watchlist_count
              FROM shadow_trades st
              JOIN profile_versions pv
                ON pv.id = st.profile_version_id
               AND pv.profile_id = st.profile_id
               AND pv.config_hash = st.profile_config_hash
               AND jsonb_typeof(pv.config) = 'object'
               AND pv.config <> '{{}}'::jsonb
              JOIN pipeline_watchlists pw
                ON pw.user_id = st.user_id
               AND pw.profile_id = st.profile_id
               AND upper(pw.level) = 'L3'
             WHERE st.source = 'L3'
               AND st.l3_consolidation_enforced IS TRUE
               AND st.config_snapshot #>> '{{consolidation,selection_mode}}' = 'single_candidate_safety_net'
               AND st.watchlist_id IS NULL
               AND st.watchlist_name IS NULL
               AND st.watchlist_level IS NULL
               AND st.lineage_confidence IS NULL
               AND st.lineage_source IS NULL
               AND st.lineage_resolved_at IS NULL
               AND (st.rules_snapshot IS NULL OR st.rules_snapshot = '{{}}'::jsonb)
             GROUP BY st.id
            HAVING count(*) = 1
        )
        INSERT INTO shadow_canonical_lineage_repair_audit (
            shadow_trade_id, repair_marker, prior_values, evidence
        )
        SELECT st.id,
               '{_MARKER}',
               jsonb_build_object(
                   'watchlist_id', st.watchlist_id,
                   'watchlist_name', st.watchlist_name,
                   'watchlist_level', st.watchlist_level,
                   'source_watchlist_id', st.source_watchlist_id,
                   'lineage_confidence', st.lineage_confidence,
                   'lineage_source', st.lineage_source,
                   'lineage_resolved_at', st.lineage_resolved_at,
                   'rules_snapshot', st.rules_snapshot
               ),
               jsonb_build_object(
                   'watchlist_id', unique_l3.watchlist_id,
                   'watchlist_count', unique_l3.watchlist_count,
                   'profile_id', st.profile_id,
                   'profile_version_id', st.profile_version_id,
                   'profile_config_hash', st.profile_config_hash,
                   'selection_mode', st.config_snapshot #>> '{{consolidation,selection_mode}}'
               )
          FROM unique_l3
          JOIN shadow_trades st ON st.id = unique_l3.shadow_trade_id
        ON CONFLICT (shadow_trade_id) DO NOTHING
    """))

    op.execute(text(f"""
        UPDATE shadow_trades st
           SET watchlist_id = pw.id,
               watchlist_name = pw.name,
               watchlist_level = pw.level,
               source_watchlist_id = pw.source_watchlist_id,
               lineage_confidence = 'JOIN_PROFILE_UNIQUE',
               lineage_source = '{_MARKER}',
               lineage_resolved_at = audit.repaired_at,
               rules_snapshot = pv.config
          FROM shadow_canonical_lineage_repair_audit audit
          JOIN pipeline_watchlists pw
            ON pw.id = (audit.evidence ->> 'watchlist_id')::uuid
          JOIN profile_versions pv
            ON pv.id = (audit.evidence ->> 'profile_version_id')::uuid
           AND pv.profile_id = (audit.evidence ->> 'profile_id')::uuid
           AND pv.config_hash = audit.evidence ->> 'profile_config_hash'
         WHERE st.id = audit.shadow_trade_id
           AND audit.repair_marker = '{_MARKER}'
           AND st.watchlist_id IS NULL
           AND st.profile_id = pw.profile_id
           AND st.user_id = pw.user_id
    """))


def downgrade() -> None:
    op.execute(text(f"""
        UPDATE shadow_trades st
           SET watchlist_id = (audit.prior_values ->> 'watchlist_id')::uuid,
               watchlist_name = audit.prior_values ->> 'watchlist_name',
               watchlist_level = audit.prior_values ->> 'watchlist_level',
               source_watchlist_id = (audit.prior_values ->> 'source_watchlist_id')::uuid,
               lineage_confidence = audit.prior_values ->> 'lineage_confidence',
               lineage_source = audit.prior_values ->> 'lineage_source',
               lineage_resolved_at = (audit.prior_values ->> 'lineage_resolved_at')::timestamptz,
               rules_snapshot = NULLIF(
                   audit.prior_values -> 'rules_snapshot', 'null'::jsonb
               )
          FROM shadow_canonical_lineage_repair_audit audit
         WHERE st.id = audit.shadow_trade_id
           AND audit.repair_marker = '{_MARKER}'
           AND st.lineage_source = '{_MARKER}'
    """))
    op.execute(text(f"""
        DELETE FROM shadow_canonical_lineage_repair_audit
         WHERE repair_marker = '{_MARKER}'
    """))
