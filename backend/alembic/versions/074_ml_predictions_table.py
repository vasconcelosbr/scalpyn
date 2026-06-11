"""Create ml_predictions table for ML inference audit trail.

Revision ID: 074_ml_predictions_table
Revises: 073_l1_spectrum_capture
Create Date: 2026-06-11

Stores one row per ML-scored signal. Two write paths:
  - L3 decision path: decision_id populated, shadow_trade_id NULL
  - L1_SPECTRUM forward scoring: shadow_trade_id populated, decision_id NULL

Table is an AUDIT LOG only — never read to make trading decisions.
Isolation invariant: no query on this table appears in any decision path.
"""

from alembic import op
import sqlalchemy as sa

revision = "074_ml_predictions_table"
down_revision = "073_l1_spectrum_capture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_predictions (
            id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            model_id             UUID        NOT NULL,
            decision_id          INTEGER,
            shadow_trade_id      UUID,
            symbol               VARCHAR     NOT NULL,
            win_fast_probability DOUBLE PRECISION NOT NULL,
            model_approved       BOOLEAN     NOT NULL DEFAULT false,
            threshold_used       DOUBLE PRECISION NOT NULL,
            scored_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_predictions_model_id
            ON ml_predictions (model_id)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_predictions_shadow_trade_id
            ON ml_predictions (shadow_trade_id)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_predictions_decision_id
            ON ml_predictions (decision_id)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_predictions_scored_at
            ON ml_predictions (scored_at DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS ml_predictions"))
