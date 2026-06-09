"""Add model_blob BYTEA column to ml_models for Railway-native model storage.

Revision ID: 070_ml_models_blob
Revises: 069_ml_models_table_fix
Create Date: 2026-06-09

Context: Cloud Run subscription cancelled. Model was stored in GCS, now stored
directly in PostgreSQL as BYTEA — no external storage dependency, accessible
from any Railway service via DATABASE_URL.

The ML Trainer serializes the model with joblib and writes the bytes into
model_blob when registering a new active model. The API loads the blob from
the active ml_models row and deserializes it in memory.
"""

from alembic import op
import sqlalchemy as sa

revision = "070_ml_models_blob"
down_revision = "069_ml_models_table_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE ml_models
            ADD COLUMN IF NOT EXISTS model_blob BYTEA
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE ml_models
            DROP COLUMN IF EXISTS model_blob
    """))
