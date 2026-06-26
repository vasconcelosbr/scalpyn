"""Add forward-validation lifecycle and controlled autonomy policy.

Revision ID: 098_forward_autonomy_policy
Revises: 097_ml_champion_registry
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa


revision = "098_forward_autonomy_policy"
down_revision = "097_ml_champion_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE algorithm_forward_validations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            suggestion_id UUID REFERENCES profile_suggestions(id) ON DELETE CASCADE,
            model_id UUID REFERENCES ml_model_registry(model_id) ON DELETE CASCADE,
            profile_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            stage VARCHAR(40) NOT NULL DEFAULT 'discovery',
            validation_status VARCHAR(40) NOT NULL DEFAULT 'exploratory_only',
            metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            human_approved_by UUID,
            human_approved_at TIMESTAMPTZ,
            rollback_payload JSONB,
            blocked_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_forward_validation_subject
                CHECK (num_nonnulls(suggestion_id, model_id) = 1),
            CONSTRAINT ck_forward_validation_stage
                CHECK (stage IN (
                    'discovery',
                    'temporal_validation',
                    'shadow_forward',
                    'human_approval',
                    'limited_live',
                    'full_live'
                )),
            CONSTRAINT ck_forward_live_prerequisites
                CHECK (
                    stage NOT IN ('limited_live', 'full_live')
                    OR (
                        validation_status = 'validated'
                        AND human_approved_by IS NOT NULL
                        AND human_approved_at IS NOT NULL
                        AND rollback_payload IS NOT NULL
                    )
                )
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX idx_forward_validation_suggestion
        ON algorithm_forward_validations(suggestion_id, stage)
    """))
    op.execute(sa.text("""
        CREATE INDEX idx_forward_validation_model
        ON algorithm_forward_validations(model_id, stage)
    """))

    op.execute(sa.text("""
        CREATE TABLE autopilot_autonomy_policies (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL UNIQUE,
            maximum_level INTEGER NOT NULL DEFAULT 2,
            impact_limit_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            cooldown_seconds INTEGER NOT NULL DEFAULT 0,
            max_changes_per_day INTEGER NOT NULL DEFAULT 0,
            risk_budget_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            post_change_monitoring BOOLEAN NOT NULL DEFAULT TRUE,
            auto_rollback_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            updated_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_autonomy_level_controlled
                CHECK (maximum_level BETWEEN 0 AND 3),
            CONSTRAINT ck_autonomy_level3_controls
                CHECK (
                    maximum_level < 3
                    OR (
                        cooldown_seconds > 0
                        AND max_changes_per_day > 0
                        AND post_change_monitoring = TRUE
                        AND impact_limit_json <> '{}'::jsonb
                        AND risk_budget_json <> '{}'::jsonb
                    )
                )
        )
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS autopilot_autonomy_policies"))
    op.execute(sa.text("DROP TABLE IF EXISTS algorithm_forward_validations"))
