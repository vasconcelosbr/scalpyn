"""Add market_type column to ohlcv table.

Revision ID: 034
Revises: 033
Create Date: 2026-05-02

Context
-------
The system now segregates SPOT and FUTURES pipelines completely.  Every row
in the ``ohlcv`` table must carry a ``market_type`` column so that:

1. Collectors tag each candle with the correct market context.
2. ``compute_indicators`` can JOIN ``ohlcv`` against ``pool_coins`` using
   both ``symbol`` and ``market_type``, preventing spot candles from being
   used to compute futures indicators (and vice versa) — relevant once the
   futures collector is active.

All existing rows receive the ``DEFAULT 'spot'`` value automatically.
This is semantically correct because the historic pipeline only produced
spot candles.

Idempotency
-----------
``ADD COLUMN IF NOT EXISTS`` makes the upgrade path safe to re-run (e.g.
during a rolling deploy with overlapping images).

Index
-----
A partial index on ``(symbol, time DESC)`` covering only
``market_type = 'futures'`` is created so futures-pipeline queries remain
fast without penalising the (dominant) spot path.

The existing unique index ``ix_ohlcv_symbol_exchange_timeframe_time`` is
intentionally left unchanged.  The ``(symbol, exchange, timeframe, time)``
combination already uniquely identifies a candle within a single market
context because spot and futures use the same ``symbol`` string but will be
distinguished by ``exchange`` (e.g. ``gate.io`` vs ``gate.io-futures``) when
the futures collector is implemented.
"""

from alembic import op
import sqlalchemy as sa

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SET LOCAL lock_timeout = '10s'"))

    op.execute(sa.text("""
        ALTER TABLE ohlcv
            ADD COLUMN IF NOT EXISTS market_type VARCHAR(10) NOT NULL DEFAULT 'spot'
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ohlcv_futures_time
            ON ohlcv (symbol, time DESC)
            WHERE market_type = 'futures'
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_ohlcv_futures_time
    """))

    op.execute(sa.text("""
        ALTER TABLE ohlcv
            DROP COLUMN IF EXISTS market_type
    """))
