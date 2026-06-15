"""Add ev_score and comparison_vs_previous to ml_models.

Revision ID: 075_ml_models_comparison
Revises: 074_ml_predictions_table
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa

revision = "075_ml_models_comparison"
down_revision = "074_ml_predictions_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS ev_score DOUBLE PRECISION
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS comparison_vs_previous JSONB
    """))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS comparison_vs_previous"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS ev_score"))
