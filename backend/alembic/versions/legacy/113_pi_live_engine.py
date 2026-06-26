"""Profile Intelligence Live Engine — 8 new tables + runs columns.

Revision ID: 113_pi_live_engine
Revises: 112_ml_gate_lineage
Create Date: 2026-06-26

Additive migration:
  - profile_intelligence_heartbeats
  - profile_intelligence_activity_log
  - profile_indicator_performance
  - profile_hard_negative_patterns
  - profile_adjustment_suggestions
  - profile_adjustment_versions
  - profile_ai_reviews
  - autopilot_pending_actions
  + 5 columns on profile_intelligence_runs
"""

from alembic import op
import sqlalchemy as sa


revision = "113_pi_live_engine"
down_revision = "112_ml_gate_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── profile_intelligence_heartbeats ─────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_heartbeats (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID NULL,
            engine_status   VARCHAR(40) NOT NULL DEFAULT 'IDLE',
            current_phase   VARCHAR(60) NOT NULL DEFAULT 'IDLE',
            heartbeat_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            next_cycle_at   TIMESTAMPTZ NULL,
            worker_name     VARCHAR(120) NULL,
            commit_hash     VARCHAR(64) NULL,
            metadata        JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_heartbeat_at
        ON profile_intelligence_heartbeats (heartbeat_at DESC)
    """))

    # ── profile_intelligence_activity_log ───────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_activity_log (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID NULL,
            event_type      VARCHAR(60) NOT NULL,
            phase           VARCHAR(60) NOT NULL,
            severity        VARCHAR(20) NOT NULL DEFAULT 'info',
            message         TEXT NOT NULL,
            profile_id      UUID NULL,
            profile_name    VARCHAR(255) NULL,
            payload         JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_activity_created
        ON profile_intelligence_activity_log (created_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_activity_profile
        ON profile_intelligence_activity_log (profile_id, created_at DESC)
        WHERE profile_id IS NOT NULL
    """))

    # ── profile_indicator_performance ───────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_indicator_performance (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id                  UUID NOT NULL,
            profile_id              UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            profile_name            VARCHAR(255) NULL,
            indicator_name          VARCHAR(80) NOT NULL,
            bucket                  VARCHAR(120) NULL,
            sample_count            INTEGER NOT NULL,
            win_count               INTEGER NOT NULL DEFAULT 0,
            loss_count              INTEGER NOT NULL DEFAULT 0,
            win_rate                NUMERIC NULL,
            avg_pnl_pct             NUMERIC NULL,
            ev_pct                  NUMERIC NULL,
            avg_mae_pct             NUMERIC NULL,
            avg_mfe_pct             NUMERIC NULL,
            avg_holding_seconds     NUMERIC NULL,
            lift_vs_profile         NUMERIC NULL,
            fpr                     NUMERIC NULL,
            metadata                JSONB NOT NULL DEFAULT '{}',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_ind_perf_run_profile
        ON profile_indicator_performance (run_id, profile_id)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_ind_perf_profile_indicator
        ON profile_indicator_performance (profile_id, indicator_name, created_at DESC)
    """))

    # ── profile_hard_negative_patterns ──────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_hard_negative_patterns (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id              UUID NOT NULL,
            profile_id          UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            profile_name        VARCHAR(255) NULL,
            pattern_key         VARCHAR(120) NOT NULL,
            pattern_payload     JSONB NOT NULL DEFAULT '{}',
            sample_count        INTEGER NOT NULL,
            loss_count          INTEGER NOT NULL,
            fp_rate             NUMERIC NULL,
            avg_loss_pct        NUMERIC NULL,
            suggested_penalty   JSONB NULL,
            status              VARCHAR(30) NOT NULL DEFAULT 'OBSERVED',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_hard_neg_profile
        ON profile_hard_negative_patterns (profile_id, created_at DESC)
    """))

    # ── profile_adjustment_suggestions ──────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_adjustment_suggestions (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id                  UUID NOT NULL,
            profile_id              UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
            profile_name            VARCHAR(255) NULL,
            suggestion_type         VARCHAR(60) NOT NULL,
            target_section          VARCHAR(80) NOT NULL,
            target_field            VARCHAR(120) NULL,
            current_value           JSONB NULL,
            suggested_value         JSONB NOT NULL,
            reason                  TEXT NOT NULL,
            evidence                JSONB NOT NULL DEFAULT '{}',
            confidence              NUMERIC NULL,
            expected_impact         JSONB NULL,
            status                  VARCHAR(40) NOT NULL DEFAULT 'PENDING_SHADOW_VALIDATION',
            mutation_applied        BOOLEAN NOT NULL DEFAULT false,
            requires_human_approval BOOLEAN NOT NULL DEFAULT true,
            rollback_payload        JSONB NULL,
            created_by              VARCHAR(60) NOT NULL DEFAULT 'profile_intelligence',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NULL,
            CONSTRAINT chk_adj_sugg_mutation
                CHECK (mutation_applied = false OR requires_human_approval = true),
            CONSTRAINT chk_adj_sugg_type_not_create
                CHECK (suggestion_type NOT IN ('CREATE_PROFILE','DUPLICATE_PROFILE','PROMOTE_LIVE','ENABLE_LIVE'))
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_adj_sugg_profile_status
        ON profile_adjustment_suggestions (profile_id, status, created_at DESC)
    """))

    # ── profile_adjustment_versions ─────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_adjustment_versions (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            suggestion_id               UUID NOT NULL REFERENCES profile_adjustment_suggestions(id) ON DELETE CASCADE,
            profile_id                  UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
            version_status              VARCHAR(40) NOT NULL,
            before_snapshot             JSONB NOT NULL,
            after_snapshot              JSONB NOT NULL,
            diff                        JSONB NOT NULL DEFAULT '{}',
            shadow_validation_status    VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            mutation_applied            BOOLEAN NOT NULL DEFAULT false,
            applied_at                  TIMESTAMPTZ NULL,
            applied_by                  VARCHAR(120) NULL,
            rollback_available          BOOLEAN NOT NULL DEFAULT true,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_adj_ver_suggestion
        ON profile_adjustment_versions (suggestion_id)
    """))

    # ── profile_ai_reviews ──────────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_ai_reviews (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id              UUID NULL,
            status              VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at        TIMESTAMPTZ NULL,
            next_review_at      TIMESTAMPTZ NULL,
            model_name          VARCHAR(60) NULL,
            prompt_hash         VARCHAR(64) NULL,
            tokens_input        INTEGER NULL,
            tokens_output       INTEGER NULL,
            summary             TEXT NULL,
            findings            JSONB NOT NULL DEFAULT '{}',
            recommendations     JSONB NOT NULL DEFAULT '[]',
            contradictions      JSONB NOT NULL DEFAULT '[]',
            risk_flags          JSONB NOT NULL DEFAULT '[]',
            raw_response_ref    TEXT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_ai_review_requested_at
        ON profile_ai_reviews (requested_at DESC)
    """))

    # ── autopilot_pending_actions ────────────────────────────────────────────────
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS autopilot_pending_actions (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            suggestion_id           UUID NULL REFERENCES profile_adjustment_suggestions(id) ON DELETE SET NULL,
            profile_id              UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
            action_type             VARCHAR(60) NOT NULL,
            action_status           VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            target_scope            VARCHAR(30) NOT NULL DEFAULT 'SHADOW',
            mutation_applied        BOOLEAN NOT NULL DEFAULT false,
            requires_human_approval BOOLEAN NOT NULL DEFAULT true,
            payload                 JSONB NOT NULL DEFAULT '{}',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NULL,
            CONSTRAINT chk_apa_mutation
                CHECK (mutation_applied = false OR requires_human_approval = true),
            CONSTRAINT chk_apa_action_type_not_create
                CHECK (action_type NOT IN ('CREATE_PROFILE','DUPLICATE_PROFILE','PROMOTE_LIVE','ENABLE_LIVE'))
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_apa_profile_status
        ON autopilot_pending_actions (profile_id, action_status, created_at DESC)
    """))

    # ── profile_intelligence_runs — add missing columns ─────────────────────────
    op.execute(sa.text("""
        ALTER TABLE profile_intelligence_runs
            ADD COLUMN IF NOT EXISTS run_type VARCHAR(30) NULL,
            ADD COLUMN IF NOT EXISTS suggestions_generated INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ai_review_requested BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS ai_review_id UUID NULL,
            ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ NULL
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS autopilot_pending_actions CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_ai_reviews CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_adjustment_versions CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_adjustment_suggestions CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_hard_negative_patterns CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_indicator_performance CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_intelligence_activity_log CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_intelligence_heartbeats CASCADE"))
    op.execute(sa.text("""
        ALTER TABLE profile_intelligence_runs
            DROP COLUMN IF EXISTS run_type,
            DROP COLUMN IF EXISTS suggestions_generated,
            DROP COLUMN IF EXISTS ai_review_requested,
            DROP COLUMN IF EXISTS ai_review_id,
            DROP COLUMN IF EXISTS finished_at
    """))
