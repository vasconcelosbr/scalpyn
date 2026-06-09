"""Create ml_models table missing from Railway DB (normally created by ML Trainer job).

Revision ID: 068_ml_models_table
Revises: 067_shadow_missing_indexes
Create Date: 2026-06-09

Context: Railway DB was bootstrapped via init_db + alembic stamp 020.
The ml_models table is normally created by the Cloud Run ML Trainer job,
not by init_db or any migration. Without it, GET /api/ml/models returns
503 Database error.

Idempotent: uses CREATE TABLE IF NOT EXISTS.
"""

from alembic import op
import sqlalchemy as sa

revision = "068_ml_models_table"
down_revision = "067_shadow_missing_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_models (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            version              VARCHAR(64)  NOT NULL,
            status               VARCHAR(32)  NOT NULL DEFAULT 'inactive',
            hyperparams          JSONB,
            train_samples        INTEGER,
            val_samples          INTEGER,
            test_samples         INTEGER,
            precision_score      DOUBLE PRECISION,
            recall_score         DOUBLE PRECISION,
            f1_score             DOUBLE PRECISION,
            roc_auc              DOUBLE PRECISION,
            win_fast_capture_rate DOUBLE PRECISION,
            false_positive_rate  DOUBLE PRECISION,
            train_from           TIMESTAMPTZ,
            train_to             TIMESTAMPTZ,
            model_path           TEXT,
            decision_threshold   DOUBLE PRECISION,
            activated_at         TIMESTAMPTZ,
            retired_at           TIMESTAMPTZ,
            notes                TEXT,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS ix_ml_models_status
            ON ml_models (status);

        CREATE INDEX IF NOT EXISTS ix_ml_models_version
            ON ml_models (version);
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS ml_models;"))
