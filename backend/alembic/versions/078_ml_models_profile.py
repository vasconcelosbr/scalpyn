"""Add profile attribution columns to ml_models for Strategy Lab training.

Revision ID: 078_ml_models_profile
Revises: 077_str_lab_profile
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

revision = "078_ml_models_profile"
down_revision = "077_str_lab_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add profile attribution columns to ml_models
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS profile_id UUID
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS profile_version TIMESTAMPTZ
    """))
    # model_scope: 'global' | 'profile' — default 'global' preserves existing rows
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS model_scope VARCHAR(20) NOT NULL DEFAULT 'global'
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS training_scope VARCHAR(32)
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS dataset_hash VARCHAR(64)
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS query_hash VARCHAR(64)
    """))
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD COLUMN IF NOT EXISTS source_filter VARCHAR(32)
    """))

    # FK: soft reference — SET NULL if profile deleted
    op.execute(sa.text("""
        ALTER TABLE ml_models
        ADD CONSTRAINT fk_ml_models_profile
        FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL
    """))

    # Indexes for model scope queries
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_ml_models_scope_profile
        ON ml_models(model_scope, profile_id, status)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_ml_models_dataset_hash
        ON ml_models(dataset_hash)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_ml_models_dataset_hash"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_ml_models_scope_profile"))
    op.execute(sa.text("""
        ALTER TABLE ml_models DROP CONSTRAINT IF EXISTS fk_ml_models_profile
    """))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS source_filter"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS query_hash"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS dataset_hash"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS training_scope"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS model_scope"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS profile_version"))
    op.execute(sa.text("ALTER TABLE ml_models DROP COLUMN IF EXISTS profile_id"))
