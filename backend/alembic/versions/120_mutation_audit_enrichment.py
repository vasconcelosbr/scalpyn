"""Mutation audit enrichment.

1. Add profile_id, mutation_applied, mutation_status, dry_run to
   profile_intelligence_audit_log.
2. Add dry_run, mutation_status to autopilot_audit_logs.
3. Create profile_indicator_mutation_links table.

Revision ID: 120_mutation_audit_enrichment
Revises: 119_shadow_closure_audit
Create Date: 2026-06-28
"""

from alembic import op

revision = "120_mutation_audit_enrichment"
down_revision = "119_shadow_closure_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. profile_intelligence_audit_log ─────────────────────────────────────
    op.execute("""
        ALTER TABLE profile_intelligence_audit_log
            ADD COLUMN IF NOT EXISTS profile_id       uuid NULL,
            ADD COLUMN IF NOT EXISTS mutation_applied boolean NULL,
            ADD COLUMN IF NOT EXISTS mutation_status  text NULL,
            ADD COLUMN IF NOT EXISTS dry_run          boolean NULL
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pi_audit_profile_id "
        "ON profile_intelligence_audit_log (profile_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pi_audit_mutation_status "
        "ON profile_intelligence_audit_log (mutation_status)"
    )

    # ── 2. autopilot_audit_logs ────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE autopilot_audit_logs
            ADD COLUMN IF NOT EXISTS dry_run         boolean NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS mutation_status text NULL
    """)

    # ── 3. profile_indicator_mutation_links ───────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS profile_indicator_mutation_links (
            id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            indicator_performance_id   uuid NULL,
            profile_id                 uuid NOT NULL,
            profile_name               text NULL,
            indicator_name             text NOT NULL,
            bucket                     text NOT NULL,
            run_id                     uuid NULL,
            suggestion_id              uuid NULL,
            autopilot_audit_log_id     uuid NULL,
            profile_adjustment_version_id uuid NULL,
            mutation_action            text NOT NULL,
            mutation_status            text NOT NULL,
            mutation_applied           boolean NOT NULL DEFAULT false,
            dry_run                    boolean NOT NULL DEFAULT true,
            evidence_json              jsonb NOT NULL DEFAULT '{}'::jsonb,
            diff_json                  jsonb NOT NULL DEFAULT '{}'::jsonb,
            ai_reason                  text NULL,
            autopilot_reason           text NULL,
            created_at                 timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_piml_profile_id_created_at "
        "ON profile_indicator_mutation_links (profile_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_piml_indicator_bucket "
        "ON profile_indicator_mutation_links (indicator_name, bucket)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_piml_mutation_status "
        "ON profile_indicator_mutation_links (mutation_status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_piml_autopilot_audit_log_id "
        "ON profile_indicator_mutation_links (autopilot_audit_log_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS profile_indicator_mutation_links")
    op.execute(
        "ALTER TABLE autopilot_audit_logs "
        "DROP COLUMN IF EXISTS dry_run, "
        "DROP COLUMN IF EXISTS mutation_status"
    )
    op.execute(
        "ALTER TABLE profile_intelligence_audit_log "
        "DROP COLUMN IF EXISTS profile_id, "
        "DROP COLUMN IF EXISTS mutation_applied, "
        "DROP COLUMN IF EXISTS mutation_status, "
        "DROP COLUMN IF EXISTS dry_run"
    )
