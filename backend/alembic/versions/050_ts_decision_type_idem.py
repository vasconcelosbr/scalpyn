"""Idempotent ADD COLUMN decision_type on trade_simulations.

Revision ID: 050_ts_decision_type_idem
Revises: 049_backfill_dl_direction
Create Date: 2026-05-12

Migration 025 created `trade_simulations` with `decision_type VARCHAR(10)
NOT NULL` via `CREATE TABLE IF NOT EXISTS`. In production the table already
existed (hand-crafted earlier or from an older schema) WITHOUT the column,
so 025 was a silent NO-OP and the column was never added. Result:
`/api/dashboard/ml-dataset` and `/ml-dataset/export` both 503'd in prod
with `column "decision_type" does not exist`.

Fix: idempotent `ADD COLUMN IF NOT EXISTS`, backfill, then add the CHECK
constraint and supporting index — all guarded by `IF NOT EXISTS` /
existence checks so re-running is safe.

NOT adding `decision_type` to `_critical_schema.CRITICAL_COLUMNS` in this
migration (rule N/N+1 — needs one prod deploy with the column present
before the validator references it).
"""

from alembic import op
import sqlalchemy as sa


revision = "050_ts_decision_type_idem"
down_revision = "049_backfill_dl_direction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add the column nullable so backfill can run without violating NOT NULL.
    op.execute(sa.text("""
        ALTER TABLE trade_simulations
            ADD COLUMN IF NOT EXISTS decision_type VARCHAR(10)
    """))

    # 2. Backfill existing rows. ALLOW is the only sensible default — every
    # historical simulation that was actually persisted came from a decision
    # the engine allowed (BLOCK decisions don't enter trade_simulations in
    # the legacy path). Future producers always set the column explicitly.
    op.execute(sa.text("""
        UPDATE trade_simulations
        SET decision_type = 'ALLOW'
        WHERE decision_type IS NULL
    """))

    # 3. Promote to NOT NULL now that no rows are NULL.
    op.execute(sa.text("""
        ALTER TABLE trade_simulations
            ALTER COLUMN decision_type SET NOT NULL
    """))

    # 4. Add the CHECK constraint (guarded — pg_constraint lookup avoids
    # duplicate-constraint error on re-run).
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'check_decision_type'
                  AND conrelid = 'trade_simulations'::regclass
            ) THEN
                ALTER TABLE trade_simulations
                    ADD CONSTRAINT check_decision_type
                    CHECK (decision_type IN ('ALLOW', 'BLOCK'));
            END IF;
        END $$
    """))

    # 5. Index (idempotent).
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_trade_simulations_decision_type
            ON trade_simulations(decision_type)
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE trade_simulations DROP CONSTRAINT IF EXISTS check_decision_type"
    ))
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_trade_simulations_decision_type"
    ))
    op.execute(sa.text(
        "ALTER TABLE trade_simulations DROP COLUMN IF EXISTS decision_type"
    ))
