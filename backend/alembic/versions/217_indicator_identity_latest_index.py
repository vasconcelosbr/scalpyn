"""Index exact indicator identity lookups without blocking writes.

Revision ID: 217_indicator_identity_idx
Revises: 216_mtf_acceptance_governance
"""

from alembic import op


revision = "217_indicator_identity_idx"
down_revision = "216_mtf_acceptance_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL rejects CREATE INDEX CONCURRENTLY inside a transaction.
    # Alembic's autocommit block is intentionally isolated in its own
    # revision so the governance tables in 216 remain atomic.
    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
              ix_indicators_identity_latest
            ON indicators (
              symbol, market_type, timeframe, scheduler_group, time DESC
            )
        """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("""
            DROP INDEX CONCURRENTLY IF EXISTS ix_indicators_identity_latest
        """)
