"""Add is_approved column to pool_coins.

Revision ID: 035
Revises: 034
Create Date: 2026-05-03

Context
-------
``pool_coins.is_approved`` is the single source of truth for the collector
symbol universe.  Only symbols with ``is_approved = true`` (and
``is_active = true``) are fetched by the market-data collectors and passed
to indicator/score computation.

Before this migration the column did not exist, so every call to
``pool_service.get_approved_pool_symbols`` raised a ProgrammingError
(``column pool_coins.is_approved does not exist``), which propagated as
``RuntimeError: All symbol collections failed`` with zero ohlcv inserts.

Default
-------
New rows default to ``false`` so that existing pool coins are not
accidentally promoted to the approved universe.  The operator must
explicitly approve each coin via the UI or a backfill UPDATE before it
is collected.

Idempotency
-----------
``ADD COLUMN IF NOT EXISTS`` makes the upgrade safe to re-run.
"""

from alembic import op
import sqlalchemy as sa

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SET LOCAL lock_timeout = '10s'"))

    op.execute(sa.text("""
        ALTER TABLE pool_coins
            ADD COLUMN IF NOT EXISTS is_approved BOOLEAN NOT NULL DEFAULT false
    """))

    # Fast index for the collector query
    # (WHERE is_active = true AND is_approved = true)
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_pool_coins_approved
            ON pool_coins (symbol, market_type)
            WHERE is_active = true AND is_approved = true
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_pool_coins_approved
    """))

    op.execute(sa.text("""
        ALTER TABLE pool_coins
            DROP COLUMN IF EXISTS is_approved
    """))
