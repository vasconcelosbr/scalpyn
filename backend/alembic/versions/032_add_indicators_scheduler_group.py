"""Add scheduler_group column to indicators table (production emergency).

Revision ID: 032
Revises: 031
Create Date: 2026-05-02

Context
-------
The ``scheduler_group`` column was added to ``init_db.py`` as a non-fatal
"best-effort" ALTER TABLE in Task #95, but was never included in any Alembic
migration.  Both ``structural_scheduler_service.py`` and
``microstructure_scheduler_service.py`` hardcode the column in their INSERT
statements with no SQL fallback, so every scheduler cycle fails in production
with ``UndefinedColumnError: column 'scheduler_group' of relation 'indicators'
does not exist``.  This produced 95 000+ errors per day and silently dropped
all indicator rows written by the two in-process schedulers.

The column groups rows by the scheduler that produced them so that
``indicator_merge.py`` can fetch the latest structural and microstructure
rows independently via a ``DISTINCT ON (symbol, scheduler_group)`` query.

Idempotency
-----------
Both DDL statements use ``IF NOT EXISTS`` / ``IF EXISTS`` guards so the
migration is safe on environments where ``init_db.py``'s best-effort path
already ran and added the column.  Running this migration twice is safe.

TimescaleDB note
----------------
``indicators`` is a TimescaleDB hypertable partitioned by ``time``.  Adding a
column with ``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` is supported by
TimescaleDB 2.x and applies to all existing and future chunks automatically.
The column is added with a non-NULL ``DEFAULT 'combined'`` so existing rows
are back-filled without a table rewrite.
"""

from alembic import op
import sqlalchemy as sa

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Set a short lock timeout so this migration fails fast rather than
    # blocking Cloud Run startup behind a long-running query on the hypertable.
    op.execute(sa.text("SET LOCAL lock_timeout = '10s'"))

    # Add the column — existing rows get DEFAULT 'combined' automatically.
    # IF NOT EXISTS guard: safe when init_db.py's best-effort already ran.
    op.execute(sa.text("""
        ALTER TABLE indicators
            ADD COLUMN IF NOT EXISTS scheduler_group VARCHAR(20) DEFAULT 'combined'
    """))

    # Composite index used by the dual-scheduler DISTINCT ON query in
    # indicator_merge.py::fetch_merged_indicators_for_symbols().
    # IF NOT EXISTS guard: safe on environments where init_db.py already
    # created this index via its best-effort block.
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_indicators_symbol_group_time
            ON indicators (symbol, scheduler_group, time DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_indicators_symbol_group_time
    """))

    op.execute(sa.text("""
        ALTER TABLE indicators
            DROP COLUMN IF EXISTS scheduler_group
    """))
