"""Create ml_models table (fix for 068 which was stamped without running DDL).

Revision ID: 069_ml_models_table_fix
Revises: 068_ml_models_table
Create Date: 2026-06-09

Context: Migration 068 used a single op.execute() with multiple semicolon-
separated SQL statements. asyncpg rejects multi-statement queries, so alembic
retried 3x, then stamped 068 as head WITHOUT running the DDL. The ml_models
table was never created, causing GET /api/ml/models → 503.

Fix: separate op.execute() calls (one statement each) — the safe pattern
with asyncpg. All statements use IF NOT EXISTS for idempotency.
"""

from alembic import op
import sqlalchemy as sa

revision = "069_ml_models_table_fix"
down_revision = "068_ml_models_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ml_models (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            version               VARCHAR(64)  NOT NULL,
            status                VARCHAR(32)  NOT NULL DEFAULT 'inactive',
            hyperparams           JSONB,
            train_samples         INTEGER,
            val_samples           INTEGER,
            test_samples          INTEGER,
            precision_score       DOUBLE PRECISION,
            recall_score          DOUBLE PRECISION,
            f1_score              DOUBLE PRECISION,
            roc_auc               DOUBLE PRECISION,
            win_fast_capture_rate DOUBLE PRECISION,
            false_positive_rate   DOUBLE PRECISION,
            train_from            TIMESTAMPTZ,
            train_to              TIMESTAMPTZ,
            model_path            TEXT,
            decision_threshold    DOUBLE PRECISION,
            activated_at          TIMESTAMPTZ,
            retired_at            TIMESTAMPTZ,
            notes                 TEXT,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_models_status
            ON ml_models (status)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_models_version
            ON ml_models (version)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS ml_models"))
