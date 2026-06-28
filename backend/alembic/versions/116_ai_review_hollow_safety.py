"""Fail-closed AI review contract and reclassification audit.

Revision ID: 116_ai_review_safety
Revises: 115_autopilot_shadow_calibration
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "116_ai_review_safety"
down_revision = "115_autopilot_shadow_calibration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_ai_review_reclassification_audit (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            review_id UUID NOT NULL REFERENCES profile_ai_reviews(id) ON DELETE RESTRICT,
            old_status VARCHAR(30) NOT NULL,
            new_status VARCHAR(30) NOT NULL,
            reason TEXT NOT NULL,
            fix_deployed_at TIMESTAMPTZ NOT NULL,
            review_snapshot JSONB NOT NULL,
            actor VARCHAR(120) NOT NULL,
            reclassified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_ai_review_reclassification UNIQUE (review_id)
        )
    """))
    op.execute(sa.text("""CREATE INDEX IF NOT EXISTS idx_ai_review_reclassification_at
                            ON profile_ai_review_reclassification_audit (reclassified_at DESC)"""))
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION enforce_completed_ai_review_contract()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.status = 'COMPLETED' AND (
                COALESCE(NEW.tokens_input, 0) <= 0
                OR COALESCE(NEW.tokens_output, 0) <= 0
                OR NULLIF(BTRIM(COALESCE(NEW.summary, '')), '') IS NULL
                OR NULLIF(BTRIM(COALESCE(NEW.model_name, '')), '') IS NULL
                OR NEW.completed_at IS NULL
            ) THEN
                RAISE EXCEPTION 'COMPLETED AI review violates fail-closed persistence contract';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_completed_ai_review_contract ON profile_ai_reviews"))
    op.execute(sa.text("""CREATE TRIGGER trg_completed_ai_review_contract
                            BEFORE INSERT OR UPDATE ON profile_ai_reviews
                            FOR EACH ROW EXECUTE FUNCTION enforce_completed_ai_review_contract()"""))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_completed_ai_review_contract ON profile_ai_reviews"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_completed_ai_review_contract()"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_ai_review_reclassification_audit"))
