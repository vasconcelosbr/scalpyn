"""Backfill exact profile lineage for shadow trades.

Revision ID: 125_shadow_profile_lineage
Revises: 124_repair_current_l3_rankings
Create Date: 2026-07-01

Only deterministic relationships are repaired: the profile attached to the
originating watchlist, or the profile already recorded on the decision log.
Ambiguous legacy L3 rows remain unassigned.
"""

from alembic import op
from sqlalchemy import text


revision = "125_shadow_profile_lineage"
down_revision = "124_repair_current_l3_rankings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The hourly uniqueness contract predates L1 profile attribution and was
    # intended for profile-generated L3 streams. L1 is sampled per execution;
    # completed observations must remain distinct. The active-trade unique
    # index remains in force and still prevents concurrent duplicates.
    op.execute(text("""
        DROP INDEX IF EXISTS uq_shadow_lab_profile_symbol_bucket
    """))
    op.execute(text("""
        CREATE UNIQUE INDEX uq_shadow_lab_profile_symbol_bucket
            ON shadow_trades (
                profile_id,
                symbol,
                source,
                shadow_lab_hour_bucket(created_at)
            )
         WHERE profile_id IS NOT NULL
           AND source <> 'L1_SPECTRUM'
    """))

    op.execute(text("""
        UPDATE shadow_trades AS st
           SET profile_id = pw.profile_id,
               profile_name = p.name,
               profile_version = COALESCE(p.profile_version, p.updated_at),
               strategy_type = 'PROFILE_L1',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_125_watchlist_profile',
               lineage_resolved_at = now()
          FROM pipeline_watchlists AS pw
          JOIN profiles AS p
            ON p.id = pw.profile_id
           AND p.user_id = pw.user_id
         WHERE st.watchlist_id = pw.id
           AND st.user_id = pw.user_id
           AND st.profile_id IS NULL
           AND pw.profile_id IS NOT NULL
           AND st.source = 'L1_SPECTRUM'
    """))

    op.execute(text("""
        UPDATE shadow_trades AS st
           SET profile_id = dl.profile_id,
               profile_name = COALESCE(dl.profile_name, p.name),
               profile_version = COALESCE(dl.profile_version, p.profile_version),
               strategy_type = 'PROFILE_L3',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_125_decision_profile',
               lineage_resolved_at = now()
          FROM decisions_log AS dl
          JOIN profiles AS p
            ON p.id = dl.profile_id
           AND p.user_id = dl.user_id
         WHERE st.decision_id = dl.id
           AND st.user_id = dl.user_id
           AND st.profile_id IS NULL
           AND dl.profile_id IS NOT NULL
           AND st.source = 'L3'
    """))

    # These decisions were created by the on-demand watchlist API before
    # it persisted profile lineage. Their exact originating watchlist IDs were
    # recovered from the archived application request logs for 2026-07-01.
    # Repair both the decision audit row and its derived shadow trade.
    op.execute(text("""
        WITH incident_decisions(decision_id, watchlist_id) AS (
            VALUES
                (121012, '29b62873-abb8-4538-a2a3-5456043c0e2f'::uuid),
                (121021, 'e43cd751-762e-49bf-aeee-d8fd1cc3a6fa'::uuid),
                (121029, '51f51586-2b4c-4208-a09b-c8f3a10d2097'::uuid),
                (121030, 'e43cd751-762e-49bf-aeee-d8fd1cc3a6fa'::uuid),
                (121031, 'aa0f91a8-d096-4337-a70f-72724243e213'::uuid),
                (121032, '1e797675-881b-404b-993f-a417a6e506b1'::uuid),
                (121033, '9100210c-58f5-4852-88e8-29d68bb228c7'::uuid)
        )
        UPDATE decisions_log AS dl
           SET profile_id = pw.profile_id,
               profile_name = p.name,
               profile_version = COALESCE(p.profile_version, p.updated_at)
          FROM incident_decisions AS incident
          JOIN pipeline_watchlists AS pw
            ON pw.id = incident.watchlist_id
          JOIN profiles AS p
            ON p.id = pw.profile_id
           AND p.user_id = pw.user_id
         WHERE dl.id = incident.decision_id
           AND dl.user_id = pw.user_id
           AND dl.profile_id IS NULL
           AND dl.strategy = 'L3'
    """))

    op.execute(text("""
        WITH incident_decisions(decision_id, watchlist_id) AS (
            VALUES
                (121012, '29b62873-abb8-4538-a2a3-5456043c0e2f'::uuid),
                (121021, 'e43cd751-762e-49bf-aeee-d8fd1cc3a6fa'::uuid),
                (121029, '51f51586-2b4c-4208-a09b-c8f3a10d2097'::uuid),
                (121030, 'e43cd751-762e-49bf-aeee-d8fd1cc3a6fa'::uuid),
                (121031, 'aa0f91a8-d096-4337-a70f-72724243e213'::uuid),
                (121032, '1e797675-881b-404b-993f-a417a6e506b1'::uuid),
                (121033, '9100210c-58f5-4852-88e8-29d68bb228c7'::uuid)
        )
        UPDATE shadow_trades AS st
           SET watchlist_id = pw.id,
               watchlist_name = pw.name,
               watchlist_level = pw.level,
               source_watchlist_id = pw.source_watchlist_id,
               profile_id = pw.profile_id,
               profile_name = p.name,
               profile_version = COALESCE(p.profile_version, p.updated_at),
               strategy_type = 'PROFILE_L3',
               lineage_confidence = 'EXACT',
               lineage_source = 'migration_125_archived_request_log',
               lineage_resolved_at = now()
          FROM incident_decisions AS incident
          JOIN pipeline_watchlists AS pw
            ON pw.id = incident.watchlist_id
          JOIN profiles AS p
            ON p.id = pw.profile_id
           AND p.user_id = pw.user_id
         WHERE st.decision_id = incident.decision_id
           AND st.user_id = pw.user_id
           AND st.profile_id IS NULL
           AND st.source = 'L3'
    """))


def downgrade() -> None:
    op.execute(text("""
        UPDATE shadow_trades
           SET profile_id = NULL,
               profile_name = NULL,
               profile_version = NULL,
               strategy_type = NULL,
               lineage_confidence = 'EXACT',
               lineage_source = 'pipeline_scan',
               lineage_resolved_at = now()
         WHERE lineage_source IN (
             'migration_125_watchlist_profile',
             'migration_125_decision_profile',
             'migration_125_archived_request_log'
         )
    """))

    op.execute(text("""
        UPDATE decisions_log
           SET profile_id = NULL,
               profile_name = NULL,
               profile_version = NULL
         WHERE id IN (121012, 121021, 121029, 121030, 121031, 121032, 121033)
           AND strategy = 'L3'
    """))

    op.execute(text("""
        DROP INDEX IF EXISTS uq_shadow_lab_profile_symbol_bucket
    """))
    op.execute(text("""
        CREATE UNIQUE INDEX uq_shadow_lab_profile_symbol_bucket
            ON shadow_trades (
                profile_id,
                symbol,
                source,
                shadow_lab_hour_bucket(created_at)
            )
         WHERE profile_id IS NOT NULL
    """))
