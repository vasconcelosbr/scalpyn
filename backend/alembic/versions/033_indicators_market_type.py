"""Add market_type column to indicators table.

Revision ID: 033
Revises: 032
Create Date: 2026-05-02

Context
-------
The system now operates with two distinct universes (SPOT and FUTURES).
Every row in the ``indicators`` hypertable must carry a ``market_type``
column so queries can filter and group by market without ambiguity
(e.g. the validation query from ETAPA 8 of the pool-segregation spec).

All existing rows and any new rows written by code that has not yet been
updated (e.g. a scheduler running an old image during a rolling deploy)
receive the ``DEFAULT 'spot'`` value automatically — backward-compatible
and semantically correct because the historic pipeline only processed
spot symbols.

Idempotency
-----------
``ADD COLUMN IF NOT EXISTS`` guards make both the upgrade and downgrade
paths safe to run more than once.  The column is added with a non-NULL
``DEFAULT 'spot'`` to avoid a table rewrite on the TimescaleDB hypertable.

Index
-----
A partial index on ``(market_type, time DESC)`` covering only
``market_type = 'futures'`` is created so the future futures pipeline
query remains fast without penalising the (dominant) spot path.

Validation query (run after deploy)
-------------------------------------
.. code-block:: sql

    SELECT
      market_type,
      COUNT(DISTINCT symbol) AS distinct_symbols,
      COUNT(*) AS row_count
    FROM indicators
    WHERE time > NOW() - INTERVAL '5 minutes'
    GROUP BY market_type
    ORDER BY market_type;

Expected output once both pipelines are running::

    market_type | distinct_symbols | row_count
    ────────────┼──────────────────┼──────────
    futures     | <futures pool #> | …
    spot        | <spot pool #>    | …
"""

from alembic import op
import sqlalchemy as sa

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Short lock timeout — fail fast rather than blocking the hypertable
    # during a Cloud Run cold-start.
    op.execute(sa.text("SET LOCAL lock_timeout = '10s'"))

    # Add the column with a DEFAULT so existing rows are back-filled without
    # a rewrite (TimescaleDB 2.x supports ADD COLUMN with DEFAULT on chunked
    # hypertables without a full-table scan).
    op.execute(sa.text("""
        ALTER TABLE indicators
            ADD COLUMN IF NOT EXISTS market_type VARCHAR(10) NOT NULL DEFAULT 'spot'
    """))

    # Partial index for futures-only queries (spot rows are the majority,
    # a full index on market_type would duplicate the dominant partition key).
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_indicators_futures_time
            ON indicators (time DESC)
            WHERE market_type = 'futures'
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_indicators_futures_time
    """))

    op.execute(sa.text("""
        ALTER TABLE indicators
            DROP COLUMN IF EXISTS market_type
    """))
