"""Add auditable ML Gate payload columns.

Revision ID: 111_ml_gate_audit_payload
Revises: 110_shadow_decision_unique
Create Date: 2026-06-25

ml_predictions must store both SCORED/ALLOW and SKIPPED/BLOCK outcomes.
The original schema required model_id, win_fast_probability and threshold_used,
which made NO_ELIGIBLE_MODEL_FOR_LANE impossible to persist.
"""

from alembic import op
import sqlalchemy as sa


revision = "111_ml_gate_audit_payload"
down_revision = "110_shadow_decision_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE ml_predictions
            ALTER COLUMN model_id DROP NOT NULL
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_predictions
            ALTER COLUMN win_fast_probability DROP NOT NULL
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_predictions
            ALTER COLUMN threshold_used DROP NOT NULL
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_predictions
            ADD COLUMN IF NOT EXISTS model_lane VARCHAR,
            ADD COLUMN IF NOT EXISTS reason_code VARCHAR,
            ADD COLUMN IF NOT EXISTS score_status VARCHAR NOT NULL DEFAULT 'SKIPPED',
            ADD COLUMN IF NOT EXISTS promotion_gate_status VARCHAR,
            ADD COLUMN IF NOT EXISTS gate_payload JSONB
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_predictions_reason_code
            ON ml_predictions (reason_code)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_predictions_model_lane
            ON ml_predictions (model_lane)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_predictions_model_approved
            ON ml_predictions (model_approved)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ml_predictions_model_approved"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ml_predictions_model_lane"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ml_predictions_reason_code"))
    op.execute(sa.text("""
        ALTER TABLE ml_predictions
            DROP COLUMN IF EXISTS gate_payload,
            DROP COLUMN IF EXISTS promotion_gate_status,
            DROP COLUMN IF EXISTS score_status,
            DROP COLUMN IF EXISTS reason_code,
            DROP COLUMN IF EXISTS model_lane
    """))
