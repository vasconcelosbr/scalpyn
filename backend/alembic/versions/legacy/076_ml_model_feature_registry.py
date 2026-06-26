"""Add feature schema registry fields to ml_models.

Revision ID: 076_ml_feature_registry
Revises: 075_ml_models_comparison
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

revision = "076_ml_feature_registry"
down_revision = "075_ml_models_comparison"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS feature_columns_json JSONB
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS feature_columns_hash VARCHAR(64)
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS feature_count INTEGER
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS feature_schema_version VARCHAR(64)
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS dataset_query_cutoff TIMESTAMPTZ
    """))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS dataset_query_cutoff"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS feature_schema_version"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS feature_count"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS feature_columns_hash"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS feature_columns_json"))
