"""Profile Intelligence global Auto-Pilot for Spot.

Revision ID: 093_pi_autopilot
Revises: 092_shadow_lab_active_dedup
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa


revision = "093_pi_autopilot"
down_revision = "092_shadow_lab_active_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_settings (
            user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            settings_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled_at TIMESTAMPTZ NULL,
            disabled_at TIMESTAMPTZ NULL,
            last_cycle_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_cycles (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            window_start TIMESTAMPTZ NOT NULL,
            idempotency_key VARCHAR(180) NOT NULL,
            status VARCHAR(40) NOT NULL,
            checkpoint VARCHAR(80) NULL,
            analysis_run_id UUID NULL REFERENCES profile_intelligence_runs(id) ON DELETE SET NULL,
            metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            errors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (idempotency_key)
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_autopilot_cycles_user_window
        ON profile_intelligence_autopilot_cycles(user_id, window_start DESC)
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_candidates (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            cycle_id UUID NULL REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE SET NULL,
            profile_id UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
            origin_profile_id UUID NULL REFERENCES profiles(id) ON DELETE SET NULL,
            previous_profile_id UUID NULL REFERENCES profiles(id) ON DELETE SET NULL,
            shadow_watchlist_id UUID NULL REFERENCES pipeline_watchlists(id) ON DELETE SET NULL,
            target_watchlist_id UUID NULL REFERENCES pipeline_watchlists(id) ON DELETE SET NULL,
            source_combination_id UUID NULL REFERENCES profile_rule_combinations(id) ON DELETE SET NULL,
            source_suggestion_id UUID NULL REFERENCES profile_suggestions(id) ON DELETE SET NULL,
            state VARCHAR(40) NOT NULL,
            canonical_signature VARCHAR(64) NOT NULL,
            canonical_rules_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            version_number INTEGER NOT NULL DEFAULT 1,
            shadow_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            review_after TIMESTAMPTZ NULL,
            observed_trades INTEGER NOT NULL DEFAULT 0,
            observed_win_rate NUMERIC(10, 6) NULL,
            observed_avg_pnl_pct NUMERIC(12, 8) NULL,
            promotion_win_rate NUMERIC(10, 6) NULL,
            promotion_avg_pnl_pct NUMERIC(12, 8) NULL,
            promoted_at TIMESTAMPTZ NULL,
            rejected_at TIMESTAMPTZ NULL,
            rollback_at TIMESTAMPTZ NULL,
            decision_reason TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (profile_id)
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_autopilot_candidates_user_state
        ON profile_intelligence_autopilot_candidates(user_id, state, created_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_autopilot_candidates_signature
        ON profile_intelligence_autopilot_candidates(user_id, canonical_signature)
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_loss_families (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            canonical_signature VARCHAR(64) NOT NULL,
            canonical_rules_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            rejection_reason TEXT NOT NULL,
            blocked_at TIMESTAMPTZ NOT NULL,
            blocked_until TIMESTAMPTZ NOT NULL,
            candidate_id UUID NULL REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, canonical_signature)
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_loss_families_active
        ON profile_intelligence_loss_families(user_id, blocked_until DESC)
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_associations (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            candidate_id UUID NULL REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL,
            watchlist_id UUID NOT NULL REFERENCES pipeline_watchlists(id) ON DELETE RESTRICT,
            previous_profile_id UUID NULL REFERENCES profiles(id) ON DELETE SET NULL,
            new_profile_id UUID NULL REFERENCES profiles(id) ON DELETE SET NULL,
            event_type VARCHAR(30) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_autopilot_assoc_watchlist
        ON profile_intelligence_autopilot_associations(user_id, watchlist_id, created_at DESC)
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_reports (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            cycle_id UUID NOT NULL REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE CASCADE,
            report_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (cycle_id)
        )
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_compensations (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            cycle_id UUID NULL REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE SET NULL,
            candidate_id UUID NULL REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL,
            operation VARCHAR(80) NOT NULL,
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            last_error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ NULL
        )
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_autopilot_audit (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            actor_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            cycle_id UUID NULL REFERENCES profile_intelligence_autopilot_cycles(id) ON DELETE SET NULL,
            candidate_id UUID NULL REFERENCES profile_intelligence_autopilot_candidates(id) ON DELETE SET NULL,
            profile_id UUID NULL REFERENCES profiles(id) ON DELETE SET NULL,
            profile_version TIMESTAMPTZ NULL,
            watchlist_id UUID NULL REFERENCES pipeline_watchlists(id) ON DELETE SET NULL,
            combination_id UUID NULL REFERENCES profile_rule_combinations(id) ON DELETE SET NULL,
            suggestion_id UUID NULL REFERENCES profile_suggestions(id) ON DELETE SET NULL,
            event_type VARCHAR(80) NOT NULL,
            input_metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            thresholds_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            decision VARCHAR(80) NULL,
            reason TEXT NULL,
            result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_autopilot_audit_user_created
        ON profile_intelligence_autopilot_audit(user_id, created_at DESC)
    """))
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION prevent_pi_autopilot_audit_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'profile_intelligence_autopilot_audit is append-only';
        END;
        $$
    """))
    op.execute(sa.text("""
        DROP TRIGGER IF EXISTS trg_pi_autopilot_audit_immutable
        ON profile_intelligence_autopilot_audit
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_pi_autopilot_audit_immutable
        BEFORE UPDATE OR DELETE ON profile_intelligence_autopilot_audit
        FOR EACH ROW EXECUTE FUNCTION prevent_pi_autopilot_audit_mutation()
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_pi_autopilot_audit_immutable ON profile_intelligence_autopilot_audit"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_pi_autopilot_audit_mutation"))
    for table in (
        "profile_intelligence_autopilot_audit",
        "profile_intelligence_autopilot_compensations",
        "profile_intelligence_autopilot_reports",
        "profile_intelligence_autopilot_associations",
        "profile_intelligence_loss_families",
        "profile_intelligence_autopilot_candidates",
        "profile_intelligence_autopilot_cycles",
        "profile_intelligence_autopilot_settings",
    ):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
